from pathlib import Path
from datetime import datetime, timezone

import networkx as nx

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.priority_manager import PriorityManager
from arch_auditor.processors import ProcessorRegistry
from arch_auditor.reporter import Reporter, ReportMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from arch_auditor.processors import Processor


class ServiceReporter(Reporter):
    def __init__(self, service: "ArchAuditService"):
        self.service = service
        self.messages: list[ReportMessage] = []

    def report(self, msg: ReportMessage):
        self.messages.append(msg)

    def list_messages(self) -> list[ReportMessage]:
        return self.messages


class ArchAuditService:
    def __init__(
        self,
        config: dict,
        registry: ProcessorRegistry | None = None,
    ):
        self.config = config
        self.priority_manager: PriorityManager | None = None

        self.time_scheduler = AsyncIOScheduler()

        self.reporter = ServiceReporter(self)
        self.arch_auditor = ArchAuditor(
            config=self.config, registry=registry, reporter=self.reporter
        )
        self.priority_manager = self.arch_auditor.priority_manager

        self.app = FastAPI(lifespan=self._generate_time_scheduler_lifespan())

        # Resolve templates relative to this module so the app can be started
        # from any working directory.
        templates_dir = Path(__file__).resolve().parent / "templates"
        self.templates = Jinja2Templates(directory=str(templates_dir))

        self._setup_routes()

    def _setup_routes(self):
        self._setup_api_routes()
        self._setup_web_routes()

    def _setup_api_routes(self):
        api = APIRouter()

        api.post("/audit")(self.trigger_audit)
        api.get("/reports")(self.list_reports)
        api.get("/service-graph")(self.get_service_graph)
        api.get("/k8s-config-issues")(self.get_k8s_config_issues)
        api.get("/circular-dependencies")(self.get_circular_dependencies)
        api.get("/dependency-edges")(self.get_dependency_edges)
        api.get("/metrics-timeseries")(self.get_metrics_timeseries)
        api.get("/resource-utilization")(self.get_resource_utilization)
        api.get("/monolithic-services")(self.get_monolithic_services)
        api.get("/single-point")(self.get_single_point)
        api.get("/over-decomposition")(self.get_over_decomposition)
        api.get("/deployment-history")(self.get_deployment_history)
        api.get("/config-drift")(self.get_config_drift)
        api.get("/maintainability")(self.get_maintainability)
        api.get("/isolation")(self.get_isolation)
        api.get("/priorities")(self.list_priorities)
        api.get("/priorities/{service}")(self.get_priority)
        api.post("/priorities/{service}/{priority}")(self.post_priority)

        self.app.include_router(api, prefix="/api")

    def _setup_web_routes(self):
        # Control panel (dashboard)
        @self.app.get("/", response_class=HTMLResponse)
        def dashboard(request: Request):
            return self.templates.TemplateResponse(
                "dashboard.html",
                {
                    "request": request,
                    "priority_enabled": self.priority_manager is not None,
                },
            )

        @self.app.get("/dashboard", response_class=HTMLResponse)
        def dashboard_alias(request: Request):
            return dashboard(request)

        @self.app.get("/ServiceAnalysisVisualization", response_class=HTMLResponse)
        def service_analysis_visualization(request: Request):
            return self.templates.TemplateResponse(
                "Service_analysis.html",
                {
                    "request": request,
                    "priority_enabled": self.priority_manager is not None,
                },
            )

        @self.app.get("/ControlCenterVisualization", response_class=HTMLResponse)
        def control_center_visualization(request: Request):
            return self.templates.TemplateResponse(
                "control_center.html",
                {
                    "request": request,
                    "priority_enabled": self.priority_manager is not None,
                },
            )

        @self.app.get("/ResourceAuditVisualization", response_class=HTMLResponse)
        def resource_audit_visualization(request: Request):
            return self.templates.TemplateResponse(
                "Resource_audit.html",
                {
                    "request": request,
                    "priority_enabled": self.priority_manager is not None,
                },
            )

    @staticmethod
    def _latest_metric_value(timeseries):
        if not isinstance(timeseries, list):
            return None
        for point in reversed(timeseries):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            _, value = point
            if value is None:
                continue
            return value
        return None

        processors_with_vis: list[Processor] = []
        for processor in self.arch_auditor.processors:
            if processor.has_visualization():
                processors_with_vis.append(processor)

        vis = APIRouter()

        def list_processors_with_vis():
            return [processor.name() for processor in processors_with_vis]

        vis.get("/")(list_processors_with_vis)

        for processor in processors_with_vis:
            vis.get(f"/{processor.name()}")(processor.visualize)

        self.app.include_router(vis, prefix="/vis")

    def _generate_time_scheduler_lifespan(self):
        async def lifespan(app: FastAPI):
            self._setup_time_scheduled_tasks()
            self.time_scheduler.start()
            yield
            self.time_scheduler.shutdown()

        return lifespan

    def _setup_time_scheduled_tasks(self):
        cron_config = self.config.get("scheduler", {}).get("cron", None)
        if cron_config:
            trigger = CronTrigger.from_crontab(cron_config)
            self.time_scheduler.add_job(
                self.trigger_audit,
                trigger=trigger,
                id="scheduled_audit_job",
                replace_existing=True,
            )

    def trigger_audit(self):
        # 先清理上一次的审计状态，防止数据累加
        self.reporter.messages.clear()
        self.arch_auditor.system_state.graph.clear()
        self.arch_auditor.system_state.extra_attrs.clear()
        
        self.arch_auditor.invoke()

    def list_reports(self):
        return [str(msg) for msg in self.reporter.list_messages()]

    def list_priorities(self):
        return dict(self.priority_manager.list_priorities())

    def get_priority(self, service: str):
        return self.priority_manager.get_priority(service)

    def post_priority(self, service: str, priority: int):
        self.priority_manager.set_priority(service, priority)
        return {"service": service, "priority": priority}

    def get_service_graph(self, view: str | None = None):
        graph = self.arch_auditor.system_state.graph
        view_name = (view or "hierarchy").lower()
        working_graph = graph
        message = None

        if view_name == "circular":
            cycles = [cycle for cycle in nx.simple_cycles(graph) if len(cycle) > 1]
            cycle_nodes = set()
            for cycle in cycles:
                cycle_nodes.update(cycle)
            if cycle_nodes:
                working_graph = graph.subgraph(cycle_nodes).copy()
            else:
                working_graph = nx.DiGraph()
                message = "No circular dependencies detected."
        elif view_name == "dominator":
            roots = [node for node in graph.nodes if graph.in_degree(node) == 0]
            if len(roots) == 1:
                try:
                    dominators = nx.immediate_dominators(graph, start=roots[0])
                    dom_graph = nx.DiGraph()
                    for node in graph.nodes:
                        dom_graph.add_node(node, **(graph.nodes[node] or {}))
                    for node, dom_node in dominators.items():
                        if node == dom_node:
                            continue
                        dom_graph.add_edge(dom_node, node)
                    working_graph = dom_graph
                except Exception:
                    working_graph = graph
                    message = "Failed to compute dominator tree."
            else:
                working_graph = graph
                message = "Dominator view requires a single root."
        priority_lookup = {}
        if self.priority_manager is not None:
            priority_lookup = dict(self.priority_manager.list_priorities())
        nodes = []
        for node in working_graph.nodes:
            attrs = graph.nodes[node] or {}
            priority = attrs.get("priority")
            if priority is None:
                priority = priority_lookup.get(node)
            try:
                priority_value = int(priority) if priority is not None else None
            except (TypeError, ValueError):
                priority_value = None

            status = "healthy"
            if priority_value == 0:
                status = "danger"
            elif priority_value == 1:
                status = "warning"

            service_level = "standard"
            if priority_value == 0:
                service_level = "critical"
            elif priority_value == 1:
                service_level = "high"
            elif priority_value == 3:
                service_level = "low"
            nodes.append(
                {
                    "id": str(node),
                    "priority": priority_value,
                    "status": status,
                    "service_level": service_level,
                    "in_degree": int(graph.in_degree(node)),
                    "out_degree": int(graph.out_degree(node)),
                }
            )

        edges = []
        for source, target, attrs in working_graph.edges(data=True):
            weight = None
            if attrs:
                weight = (
                    attrs.get("call_count")
                    or attrs.get("callCount")
                    or attrs.get("weight")
                )
            edges.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "weight": weight,
                }
            )

        return {
            "nodes": nodes,
            "edges": edges,
            "view": view_name,
            "message": message,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_k8s_config_issues(self):
        issues = self.arch_auditor.system_state.extra_attrs.get("k8s_issues", []) or []
        normalized = []
        for item in issues:
            normalized.append(
                {
                    "severity": item.get("severity"),
                    "type": item.get("type"),
                    "message": item.get("message"),
                    "resource": item.get("resource"),
                }
            )
        normalized.sort(
            key=lambda item: (
                item.get("severity") or "",
                item.get("type") or "",
                item.get("resource") or "",
            )
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normalized),
            "items": normalized,
        }

    def get_circular_dependencies(self):
        summary = (
            self.arch_auditor.system_state.extra_attrs.get(
                "circular_dependency_summary", {}
            )
            or {}
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": summary.get("count", 0),
            "cycles": summary.get("cycles", []),
        }

    def get_dependency_edges(self):
        graph = self.arch_auditor.system_state.graph
        edges = []
        for source, target, attrs in graph.edges(data=True):
            attrs = attrs or {}
            source_node = graph.nodes.get(source, {})
            target_node = graph.nodes.get(target, {})
            edges.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "dependency_type": attrs.get("dependency_type", "unknown"),
                    "call_count": attrs.get("call_count") or attrs.get("callCount"),
                    "dependency_correlation": attrs.get("dependency_correlation"),
                    "dependency_correlation_status": attrs.get(
                        "dependency_correlation_status"
                    ),
                    "edge_span_count": attrs.get("edge_span_count"),
                    "edge_avg_duration_ms": attrs.get("edge_avg_duration_ms"),
                    "edge_max_duration_ms": attrs.get("edge_max_duration_ms"),
                    "edge_error_count": attrs.get("edge_error_count"),
                    "edge_error_rate": attrs.get("edge_error_rate"),
                    "source_latest_latency": self._latest_metric_value(
                        source_node.get("latency")
                    ),
                    "target_latest_latency": self._latest_metric_value(
                        target_node.get("latency")
                    ),
                    "source_latest_error_rate": self._latest_metric_value(
                        source_node.get("error_rate")
                    ),
                    "target_latest_error_rate": self._latest_metric_value(
                        target_node.get("error_rate")
                    ),
                    "source_latest_throughput": self._latest_metric_value(
                        source_node.get("throughput")
                    ),
                    "target_latest_throughput": self._latest_metric_value(
                        target_node.get("throughput")
                    ),
                }
            )

        edges.sort(key=lambda item: (item["source"], item["target"]))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(edges),
            "edges": edges,
        }

    def get_metrics_timeseries(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        metrics_timeseries = extra_attrs.get("metrics_timeseries", {}) or {}
        raw_metrics_timeseries = extra_attrs.get("raw_metrics_timeseries", {}) or {}
        metric_aliases = extra_attrs.get("metrics_aliases", {}) or {}
        prometheus_queries = extra_attrs.get("prometheus_queries", {}) or {}
        prometheus_query_windows = (
            extra_attrs.get("prometheus_query_windows", {}) or {}
        )
        prometheus_metric_errors = (
            extra_attrs.get("prometheus_metric_errors", {}) or {}
        )
        prometheus_metric_fallbacks = (
            extra_attrs.get("prometheus_metric_fallbacks", {}) or {}
        )
        prometheus_metric_attempts = (
            extra_attrs.get("prometheus_metric_attempts", {}) or {}
        )

        summary = {}
        for metric_name, services_data in metrics_timeseries.items():
            summary[metric_name] = []
            for service_name, points in services_data.items():
                summary[metric_name].append(
                    {
                        "service": service_name,
                        "points": len(points or []),
                        "sample": (points or [])[:5],
                    }
                )
            summary[metric_name].sort(key=lambda item: item["service"])

        raw_summary = {}
        for metric_name, services_data in raw_metrics_timeseries.items():
            raw_summary[metric_name] = []
            for service_name, points in services_data.items():
                raw_summary[metric_name].append(
                    {
                        "service": service_name,
                        "mapped_service": metric_aliases.get(service_name),
                        "points": len(points or []),
                        "sample": (points or [])[:5],
                    }
                )
            raw_summary[metric_name].sort(key=lambda item: item["service"])

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queries": prometheus_queries,
            "query_windows": prometheus_query_windows,
            "metric_errors": prometheus_metric_errors,
            "metric_fallbacks": prometheus_metric_fallbacks,
            "metric_attempts": prometheus_metric_attempts,
            "aliases": metric_aliases,
            "raw_metrics": raw_summary,
            "metrics": summary,
        }

    def get_resource_utilization(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("resource_utilization_summary", []) or []

        normalized = []
        for item in summary:
            normalized.append(
                {
                    "service": item.get("service"),
                    "container": item.get("container"),
                    "request_cpu": item.get("request_cpu"),
                    "limit_cpu": item.get("limit_cpu"),
                    "request_memory_bytes": item.get("request_memory_bytes"),
                    "limit_memory_bytes": item.get("limit_memory_bytes"),
                    "avg_cpu_usage": item.get("avg_cpu_usage"),
                    "short_term_avg_cpu_usage": item.get(
                        "short_term_avg_cpu_usage"
                    ),
                    "avg_memory_usage_bytes": item.get("avg_memory_usage_bytes"),
                    "short_term_avg_memory_usage_bytes": item.get(
                        "short_term_avg_memory_usage_bytes"
                    ),
                    "avg_throughput": item.get("avg_throughput"),
                    "cluster_median_throughput": item.get(
                        "cluster_median_throughput"
                    ),
                    "high_qps_multiplier": item.get("high_qps_multiplier"),
                    "cpu_timeseries_points": item.get("cpu_timeseries_points"),
                    "memory_timeseries_points": item.get("memory_timeseries_points"),
                    "throughput_timeseries_points": item.get(
                        "throughput_timeseries_points"
                    ),
                    "is_sync_path_service": item.get("is_sync_path_service", False),
                    "is_async_only_service": item.get("is_async_only_service", False),
                    "is_high_qps_service": item.get("is_high_qps_service", False),
                    "requires_request": item.get("requires_request", False),
                    "missing_request_reason": item.get("missing_request_reason"),
                    "sync_entry_services": item.get("sync_entry_services", []),
                    "has_missing_request_issue": item.get(
                        "has_missing_request_issue", False
                    ),
                    "has_cpu_waste_issue": item.get("has_cpu_waste_issue", False),
                    "has_memory_waste_issue": item.get(
                        "has_memory_waste_issue", False
                    ),
                    "has_waste_issue": item.get("has_waste_issue", False),
                    "has_cpu_limit_risk_issue": item.get(
                        "has_cpu_limit_risk_issue", False
                    ),
                    "has_memory_limit_risk_issue": item.get(
                        "has_memory_limit_risk_issue", False
                    ),
                    "has_limit_risk_issue": item.get(
                        "has_limit_risk_issue", False
                    ),
                }
            )

        normalized.sort(key=lambda item: (item["service"] or "", item["container"] or ""))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normalized),
            "items": normalized,
        }

    def get_monolithic_services(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("monolithic_service_summary", []) or []

        normalized = []
        for item in summary:
            normalized.append(
                {
                    "service": item.get("service"),
                    "in_degree": item.get("in_degree"),
                    "out_degree": item.get("out_degree"),
                    "degree": item.get("degree"),
                    "degree_threshold": item.get("degree_threshold"),
                    "has_architecture_issue": item.get(
                        "has_architecture_issue", False
                    ),
                    "avg_cpu_usage": item.get("avg_cpu_usage"),
                    "avg_memory_usage_bytes": item.get("avg_memory_usage_bytes"),
                    "cpu_timeseries_points": item.get("cpu_timeseries_points", 0),
                    "memory_timeseries_points": item.get(
                        "memory_timeseries_points", 0
                    ),
                    "cluster_median_avg_cpu_usage": item.get(
                        "cluster_median_avg_cpu_usage"
                    ),
                    "cluster_median_avg_memory_usage_bytes": item.get(
                        "cluster_median_avg_memory_usage_bytes"
                    ),
                    "total_request_cpu": item.get("total_request_cpu"),
                    "total_limit_cpu": item.get("total_limit_cpu"),
                    "total_request_memory_bytes": item.get(
                        "total_request_memory_bytes"
                    ),
                    "total_limit_memory_bytes": item.get("total_limit_memory_bytes"),
                    "has_resource_cpu_monolith_issue": item.get(
                        "has_resource_cpu_monolith_issue", False
                    ),
                    "has_resource_memory_monolith_issue": item.get(
                        "has_resource_memory_monolith_issue", False
                    ),
                    "has_resource_monolith_issue": item.get(
                        "has_resource_monolith_issue", False
                    ),
                }
            )

        normalized.sort(key=lambda item: item["service"] or "")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normalized),
            "items": normalized,
        }

    def get_over_decomposition(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("over_decomposition_summary", {}) or {}

        normalized = {
            "root_services": summary.get("root_services", []),
            "root_count": summary.get("root_count", 0),
            "longest_path_services": summary.get("longest_path_services", []),
            "longest_path_service_count": summary.get(
                "longest_path_service_count", 0
            ),
            "path_service_threshold": summary.get("path_service_threshold"),
            "longest_path_total_avg_latency": summary.get(
                "longest_path_total_avg_latency"
            ),
            "has_long_chain_issue": summary.get("has_long_chain_issue", False),
            "pipe_services": summary.get("pipe_services", []),
            "pipe_service_ratio": summary.get("pipe_service_ratio", 0.0),
            "pipe_service_ratio_threshold": summary.get(
                "pipe_service_ratio_threshold"
            ),
            "has_pipe_service_ratio_issue": summary.get(
                "has_pipe_service_ratio_issue", False
            ),
            "fanout_amplification_services": summary.get(
                "fanout_amplification_services", []
            ),
            "fanout_amplification_threshold": summary.get(
                "fanout_amplification_threshold"
            ),
            "has_fanout_amplification_issue": summary.get(
                "has_fanout_amplification_issue", False
            ),
            "co_deployed_pairs": summary.get("co_deployed_pairs", []),
            "co_deploy_overlap_threshold": summary.get(
                "co_deploy_overlap_threshold"
            ),
        }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": normalized,
        }

    def get_single_point(self):
        summary = (
            self.arch_auditor.system_state.extra_attrs.get(
                "single_point_summary", {}
            )
            or {}
        )
        normalized = {
            "root": summary.get("root"),
            "root_count": summary.get("root_count"),
            "warning_percentage_threshold": summary.get(
                "warning_percentage_threshold"
            ),
            "critical_nodes": summary.get("critical_nodes", []),
            "availability_risk_nodes": summary.get("availability_risk_nodes", []),
            "criticality_scores": summary.get("criticality_scores", {}),
            "dominator_tree": summary.get("dominator_tree", {}),
        }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": normalized,
        }

    def get_deployment_history(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        events = extra_attrs.get("deployment_history", []) or []
        source = extra_attrs.get("deployment_history_source")

        normalized = []
        for event in events:
            normalized.append(
                {
                    "service": event.get("service"),
                    "namespace": event.get("namespace"),
                    "resource": event.get("resource"),
                    "kind": event.get("kind"),
                    "action": event.get("action"),
                    "version": event.get("version"),
                    "deployed_at": event.get("deployed_at"),
                    "success": event.get("success"),
                    "event_source": event.get("event_source"),
                    "verb": event.get("verb"),
                    "username": event.get("username"),
                    "user_agent": event.get("user_agent"),
                    "source_ip": event.get("source_ip"),
                    "change_cause": event.get("change_cause"),
                }
            )

        normalized.sort(
            key=lambda item: (
                item.get("service") or "",
                item.get("deployed_at") or "",
            )
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "count": len(normalized),
            "items": normalized,
        }

    def get_config_drift(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("config_drift_summary", []) or []

        normalized = []
        for item in summary:
            normalized.append(
                {
                    "resource": item.get("resource"),
                    "namespace": item.get("namespace"),
                    "kind": item.get("kind"),
                    "name": item.get("name"),
                    "revision": item.get("revision"),
                    "latest_changed_at": item.get("latest_changed_at"),
                    "latest_changed_by": item.get("latest_changed_by"),
                    "manager": item.get("manager"),
                    "reason": item.get("reason"),
                    "change_cause": item.get("change_cause"),
                    "event_count": item.get("event_count", 0),
                    "age_hours": item.get("age_hours"),
                    "drift_threshold_hours": item.get("drift_threshold_hours"),
                    "is_manual_change": item.get("is_manual_change", False),
                    "is_drifted": item.get("is_drifted", False),
                    "verb": item.get("verb"),
                    "username": item.get("username"),
                    "user_agent": item.get("user_agent"),
                    "source_ip": item.get("source_ip"),
                    "event_source": item.get("event_source"),
                    "selected_event_strategy": item.get("selected_event_strategy"),
                    "latest_observed_at": item.get("latest_observed_at"),
                    "latest_observed_by": item.get("latest_observed_by"),
                    "latest_observed_manager": item.get(
                        "latest_observed_manager"
                    ),
                    "latest_observed_reason": item.get("latest_observed_reason"),
                    "latest_observed_verb": item.get("latest_observed_verb"),
                }
            )

        normalized.sort(key=lambda item: item.get("resource") or "")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normalized),
            "items": normalized,
        }

    def get_maintainability(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("maintainability_summary", {}) or {}

        normalized = {
            "startup_threshold_seconds": summary.get("startup_threshold_seconds"),
            "rollback_ratio_threshold": summary.get("rollback_ratio_threshold"),
            "low_deploy_frequency_ratio": summary.get(
                "low_deploy_frequency_ratio"
            ),
            "co_deploy_overlap_threshold": summary.get(
                "co_deploy_overlap_threshold"
            ),
            "min_co_deploy_events_per_service": summary.get(
                "min_co_deploy_events_per_service"
            ),
            "deployment_history_source": summary.get(
                "deployment_history_source"
            ),
            "history_observability": summary.get("history_observability", {}),
            "startup_issues": sorted(
                summary.get("startup_issues", []),
                key=lambda item: (
                    item.get("service") or "",
                    item.get("pod") or "",
                ),
            ),
            "deploy_frequency_issues": sorted(
                summary.get("deploy_frequency_issues", []),
                key=lambda item: item.get("service") or "",
            ),
            "rollback_issues": sorted(
                summary.get("rollback_issues", []),
                key=lambda item: item.get("service") or "",
            ),
            "co_deployment_issues": sorted(
                summary.get("co_deployment_issues", []),
                key=lambda item: (
                    item.get("service_a") or "",
                    item.get("service_b") or "",
                ),
            ),
        }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": normalized,
        }

    def get_isolation(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        summary = extra_attrs.get("isolation_summary", {}) or {}

        normalized = {
            "analyzed_services": summary.get("analyzed_services", []),
            "min_replicas_for_spread": summary.get(
                "min_replicas_for_spread"
            ),
            "placements": summary.get("placements", []),
            "co_located_service_groups": summary.get(
                "co_located_service_groups", []
            ),
            "same_node_replica_services": summary.get(
                "same_node_replica_services", []
            ),
            "single_zone_services": summary.get("single_zone_services", []),
            "service_zone_spread": summary.get("service_zone_spread", []),
            "node_details": summary.get("node_details", {}),
        }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": normalized,
        }

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        import uvicorn

        uvicorn.run(self.app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Arch Audit Service")
    parser.add_argument(
        "--config", type=str, required=True, help="Path to the configuration YAML file"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to run the service on"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to run the service on"
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if config is None:
        config = {}

    service = ArchAuditService(config=config)
    service.run(host=args.host, port=args.port)
