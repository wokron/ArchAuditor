from pathlib import Path
from datetime import datetime, timezone

import networkx as nx

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from arch_auditor.priority_manager import PriorityManager
from arch_auditor.arch_auditor import ArchAuditor
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
        self.priority_manager = None
        self.config = config

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
        api.get("/dependency-edges")(self.get_dependency_edges)
        api.get("/metrics-timeseries")(self.get_metrics_timeseries)
        api.get("/resource-utilization")(self.get_resource_utilization)
        api.get("/monolithic-services")(self.get_monolithic_services)
        api.get("/over-decomposition")(self.get_over_decomposition)
        api.get("/deployment-history")(self.get_deployment_history)

        if self.priority_manager is not None:
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
                },
            )

        @self.app.get("/ControlCenterVisualization", response_class=HTMLResponse)
        def control_center_visualization(request: Request):
            return self.templates.TemplateResponse(
                "control_center.html",
                {
                    "request": request,
                },
            )

        @self.app.get("/ResourceAuditVisualization", response_class=HTMLResponse)
        def resource_audit_visualization(request: Request):
            return self.templates.TemplateResponse(
                "Resource_audit.html",
                {
                    "request": request,
                },
            )

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

    def list_priorities(self):
        return dict(self.priority_manager.list_priorities())

    def get_priority(self, service):
        return self.priority_manager.get_priority(service)

    def post_priority(self, service: str, priority: int):
        self.priority_manager.set_priority(service, priority)
        return {"service": service, "priority": priority}

    def list_reports(self):
        return [str(msg) for msg in self.reporter.list_messages()]

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
        nodes = []
        priority_lookup = {}
        if self.priority_manager is not None:
            try:
                priority_lookup = dict(self.priority_manager.list_priorities())
            except Exception:
                priority_lookup = {}

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
            elif priority_value == 2:
                service_level = "standard"
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

    def get_dependency_edges(self):
        graph = self.arch_auditor.system_state.graph
        edges = []
        for source, target, attrs in graph.edges(data=True):
            attrs = attrs or {}
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
                    "priority": item.get("priority"),
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
                    "cpu_timeseries_points": item.get("cpu_timeseries_points"),
                    "memory_timeseries_points": item.get("memory_timeseries_points"),
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
            "co_deployed_pairs": summary.get("co_deployed_pairs", []),
            "co_deploy_overlap_threshold": summary.get(
                "co_deploy_overlap_threshold"
            ),
        }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": normalized,
        }

    def get_deployment_history(self):
        extra_attrs = self.arch_auditor.system_state.extra_attrs
        events = extra_attrs.get("deployment_history", []) or []

        normalized = []
        for event in events:
            normalized.append(
                {
                    "service": event.get("service"),
                    "action": event.get("action"),
                    "version": event.get("version"),
                    "deployed_at": event.get("deployed_at"),
                    "success": event.get("success"),
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
            "count": len(normalized),
            "items": normalized,
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

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)
    if config is None:
        config = {}

    service = ArchAuditService(config=config)
    service.run(host=args.host, port=args.port)
