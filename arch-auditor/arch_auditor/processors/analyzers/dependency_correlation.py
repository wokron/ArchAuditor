import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from arch_auditor.visualization_graph import build_runtime_nodes


class DependencyCorrelationAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "DependencyCorrelationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceDependencySource", "ServicePrioritySource"]

    def init(self, config) -> bool:
        config = config or {}
        self.warning_correlation_threshold = float(
            config.get("warning_correlation_threshold", 0.7)
        )
        self.error_correlation_threshold = float(
            config.get("error_correlation_threshold", 0.9)
        )
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            return

        summary = {
            "priority_order": "P0 has the highest availability requirement; larger numbers are lower priority.",
            "strong_dependency_violations": [],
            "weak_dependency_violations": [],
            "missing_priority_edges": [],
            "strong_edges": [],
            "weak_edges": [],
        }

        for source, target, edge_data in graph.edges(data=True):
            edge_data = edge_data or {}
            correlation_status = edge_data.get("dependency_correlation_status")
            correlation = edge_data.get("dependency_correlation")
            dependency_type = self._dependency_type(edge_data)
            source_priority = self._priority(graph, source)
            target_priority = self._priority(graph, target)
            edge_data["source_priority"] = source_priority
            edge_data["target_priority"] = target_priority

            if dependency_type == "strong":
                summary["strong_edges"].append(self._edge_summary(source, target, edge_data))
                self._check_strong_priority_constraint(
                    graph,
                    source,
                    target,
                    edge_data,
                    summary,
                )
            elif dependency_type == "weak":
                summary["weak_edges"].append(self._edge_summary(source, target, edge_data))

            if correlation_status != "ok" or correlation is None:
                continue

            if correlation < self.warning_correlation_threshold:
                continue

            report_type = (
                ReportType.ERROR
                if correlation >= self.error_correlation_threshold
                else ReportType.WARNING
            )
            call_count = edge_data.get("call_count")
            call_count_suffix = (
                f", call_count: {call_count}" if call_count is not None else ""
            )
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=report_type,
                    message=(
                        "Strong dependency detected from Pearson correlation: "
                        f"'{source}' -> '{target}' has correlation "
                        f"{correlation:.4f}{call_count_suffix}."
                    ),
                )
            )

        self._check_weak_priority_constraints(graph, summary)
        self.context.system_state.extra_attrs["dependency_priority_summary"] = summary

    def _dependency_type(self, edge_data: dict) -> str:
        dependency_type = str(edge_data.get("dependency_type") or "").lower()
        correlation_status = str(edge_data.get("dependency_correlation_status") or "").lower()
        correlation = edge_data.get("dependency_correlation")
        if dependency_type in {"strong", "weak"}:
            return dependency_type
        if (
            correlation_status == "ok"
            and correlation is not None
            and correlation >= self.warning_correlation_threshold
        ):
            return "strong"
        return "unknown"

    @staticmethod
    def _priority(graph, service) -> int | None:
        value = graph.nodes.get(service, {}).get("priority")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _edge_summary(self, source, target, edge_data: dict) -> dict:
        return {
            "source": str(source),
            "target": str(target),
            "dependency_type": self._dependency_type(edge_data),
            "source_priority": edge_data.get("source_priority"),
            "target_priority": edge_data.get("target_priority"),
            "dependency_correlation": edge_data.get("dependency_correlation"),
            "dependency_correlation_status": edge_data.get(
                "dependency_correlation_status"
            ),
            "call_count": edge_data.get("call_count") or edge_data.get("callCount"),
            "priority_violation": edge_data.get("priority_violation", False),
            "priority_violation_reason": edge_data.get("priority_violation_reason"),
        }

    def _missing_priority(self, source, target, edge_data: dict, summary: dict) -> bool:
        missing = []
        if edge_data.get("source_priority") is None:
            missing.append(str(source))
        if edge_data.get("target_priority") is None:
            missing.append(str(target))
        if not missing:
            return False
        edge_data["priority_check_status"] = "missing_priority"
        summary["missing_priority_edges"].append(
            {
                "source": str(source),
                "target": str(target),
                "missing_services": sorted(set(missing)),
            }
        )
        return True

    def _check_strong_priority_constraint(
        self, graph, source, target, edge_data: dict, summary: dict
    ) -> None:
        if self._missing_priority(source, target, edge_data, summary):
            return

        source_priority = edge_data["source_priority"]
        target_priority = edge_data["target_priority"]
        if target_priority <= source_priority:
            edge_data["priority_check_status"] = "ok"
            return

        edge_data["priority_violation"] = True
        edge_data["priority_violation_kind"] = "strong_dependency_lower_priority"
        edge_data["priority_violation_reason"] = (
            "强依赖要求下游服务优先级不低于上游服务"
        )
        violation = self._edge_summary(source, target, edge_data)
        summary["strong_dependency_violations"].append(violation)
        self.context.reporter.report(
            ReportMessage(
                report_from=self.name(),
                report_type=ReportType.ERROR,
                message=(
                    "Priority violation: strong dependency "
                    f"'{source}'(P{source_priority}) -> '{target}'(P{target_priority}) "
                    "breaks the rule that a strongly depended-on service must not "
                    "have lower priority than its caller."
                ),
            )
        )

    def _check_weak_priority_constraints(self, graph, summary: dict) -> None:
        for target in graph.nodes:
            if str(target) == "<SOURCE>":
                continue
            incoming = []
            for source in graph.predecessors(target):
                if str(source) == "<SOURCE>":
                    continue
                edge_data = graph.edges[source, target]
                dependency_type = self._dependency_type(edge_data)
                if dependency_type in {"strong", "weak"}:
                    incoming.append((source, target, edge_data, dependency_type))
            if not incoming or any(item[3] == "strong" for item in incoming):
                continue

            for source, target, edge_data, _dependency_type in incoming:
                if self._missing_priority(source, target, edge_data, summary):
                    continue
                source_priority = edge_data["source_priority"]
                target_priority = edge_data["target_priority"]
                if target_priority >= source_priority:
                    edge_data.setdefault("priority_check_status", "ok")
                    continue

                edge_data["priority_violation"] = True
                edge_data["priority_violation_kind"] = "weak_only_target_higher_priority"
                edge_data["priority_violation_reason"] = (
                    "仅被弱依赖的服务不应配置得比依赖它的服务更高优先级"
                )
                violation = self._edge_summary(source, target, edge_data)
                summary["weak_dependency_violations"].append(violation)
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            "Priority violation: weak dependency "
                            f"'{source}'(P{source_priority}) -> '{target}'(P{target_priority}) "
                            "suggests the weakly depended-on service is configured "
                            "with higher priority than its caller."
                        ),
                    )
                )

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        graph = self.context.system_state.graph

        def latest_metric(node, metric_name: str):
            series = graph.nodes.get(node, {}).get(metric_name)
            if not isinstance(series, list):
                return None
            for point in reversed(series):
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                _timestamp, value = point
                if value is not None:
                    return value
            return None

        def edge_signals(source, target, attrs: dict) -> dict:
            edge_error_rate = attrs.get("edge_error_rate") or 0
            source_error_rate = latest_metric(source, "error_rate") or 0
            target_error_rate = latest_metric(target, "error_rate") or 0
            error_rate = max(edge_error_rate, source_error_rate, target_error_rate)
            error_count = attrs.get("edge_error_count") or 0

            edge_latency = attrs.get("edge_avg_duration_ms") or 0
            source_latency = latest_metric(source, "latency") or 0
            target_latency = latest_metric(target, "latency") or 0
            latency = max(edge_latency, source_latency, target_latency)

            correlation = attrs.get("dependency_correlation")
            dependency_type = self._dependency_type(attrs)
            source_priority = attrs.get("source_priority")
            target_priority = attrs.get("target_priority")
            priority_violation = bool(attrs.get("priority_violation"))
            violation_kind = attrs.get("priority_violation_kind")
            missing_priority = attrs.get("priority_check_status") == "missing_priority"

            if priority_violation and violation_kind == "strong_dependency_lower_priority":
                severity = "strong_violation"
            elif priority_violation:
                severity = "weak_violation"
            elif missing_priority and dependency_type in {"strong", "weak"}:
                severity = "missing_priority"
            elif dependency_type == "strong":
                severity = "strong"
            elif dependency_type == "weak":
                severity = "weak"
            else:
                severity = "normal"

            return {
                "severity": severity,
                "dependency_type": dependency_type,
                "source_priority": source_priority,
                "target_priority": target_priority,
                "priority_violation": priority_violation,
                "priority_violation_reason": attrs.get("priority_violation_reason"),
                "error_rate": error_rate,
                "error_count": error_count,
                "latency": latency,
                "correlation": correlation,
                "call_count": attrs.get("call_count") or attrs.get("callCount"),
            }

        node_severity = {}
        risky_edges = []
        edges = []

        def promote_node(node_id: str, severity: str) -> None:
            priority = {
                "normal": 0,
                "weak": 1,
                "strong": 2,
                "missing_priority": 3,
                "weak_violation": 4,
                "strong_violation": 5,
            }
            current = node_severity.get(node_id, "normal")
            if priority.get(severity, 0) > priority.get(current, 0):
                node_severity[node_id] = severity

        def priority_label(value) -> str:
            return f"P{value}" if value is not None else "P?"

        for source, target, attrs in graph.edges(data=True):
            source_id = str(source)
            target_id = str(target)
            if source_id == "<SOURCE>" or target_id == "<SOURCE>":
                continue

            signals = edge_signals(source, target, attrs or {})
            promote_node(source_id, signals["severity"])
            promote_node(target_id, signals["severity"])
            if signals["severity"] != "normal":
                risky_edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        **signals,
                    }
                )

            priority_suffix = (
                f"{priority_label(signals['source_priority'])}->{priority_label(signals['target_priority'])}"
            )
            if signals["severity"] == "strong_violation":
                style = {
                    "stroke": "#ef4444",
                    "lineWidth": 4,
                    "endArrow": {"path": "M 0,0 L 10,4 L 10,-4 Z", "fill": "#ef4444"},
                }
                label = f"strong {priority_suffix}"
            elif signals["severity"] == "weak_violation":
                style = {
                    "stroke": "#f59e0b",
                    "lineWidth": 3.5,
                    "endArrow": {"path": "M 0,0 L 10,4 L 10,-4 Z", "fill": "#f59e0b"},
                }
                label = f"weak {priority_suffix}"
            elif signals["severity"] == "missing_priority":
                style = {
                    "stroke": "#64748b",
                    "lineWidth": 2.5,
                    "lineDash": [4, 4],
                    "endArrow": {"path": "M 0,0 L 10,4 L 10,-4 Z", "fill": "#64748b"},
                }
                label = f"{signals['dependency_type']} {priority_suffix}"
            elif signals["severity"] == "strong":
                style = {
                    "stroke": "#8b5cf6",
                    "lineWidth": 3,
                    "endArrow": {"path": "M 0,0 L 10,4 L 10,-4 Z", "fill": "#8b5cf6"},
                }
                label = f"strong {priority_suffix}"
            elif signals["severity"] == "weak":
                style = {
                    "stroke": "#38bdf8",
                    "lineWidth": 2.5,
                    "endArrow": {"path": "M 0,0 L 10,4 L 10,-4 Z", "fill": "#38bdf8"},
                }
                label = f"weak {priority_suffix}"
            else:
                style = {}
                label = (
                    f"calls: {signals['call_count']}"
                    if signals["call_count"] is not None
                    else None
                )

            edge_data = {"source": source_id, "target": target_id}
            if style:
                edge_data["style"] = style
            if label:
                edge_data["label"] = label
            edges.append(edge_data)

        service_priorities = (
            self.context.system_state.extra_attrs.get("service_priorities", {}) or {}
        )
        default_priority = self.context.system_state.extra_attrs.get(
            "service_priority_default"
        )

        def display_priority(node_id: str):
            priority = None
            if graph.has_node(node_id):
                priority = self._priority(graph, node_id)
            if priority is None:
                priority = service_priorities.get(node_id, default_priority)
            try:
                return int(priority) if priority is not None else None
            except (TypeError, ValueError):
                return None

        def node_style(node_id: str, has_trace: bool, _is_k8s_known: bool) -> dict:
            if not has_trace:
                return {
                    "fill": "#e2e8f0",
                    "stroke": "#94a3b8",
                    "lineWidth": 1.5,
                    "lineDash": [5, 4],
                }
            severity = node_severity.get(node_id, "normal")
            if severity == "strong_violation":
                return {"fill": "#ef4444", "stroke": "#991b1b", "lineWidth": 3}
            if severity == "weak_violation":
                return {"fill": "#f59e0b", "stroke": "#b45309", "lineWidth": 2.5}
            if severity == "missing_priority":
                return {"fill": "#94a3b8", "stroke": "#475569", "lineWidth": 2.5}
            if severity == "strong":
                return {"fill": "#8b5cf6", "stroke": "#5b21b6", "lineWidth": 2.5}
            if severity == "weak":
                return {"fill": "#38bdf8", "stroke": "#0369a1", "lineWidth": 2}
            return {"fill": "#cbd5e1", "stroke": "#64748b", "lineWidth": 1.5}

        def node_label(node_id: str) -> str:
            return f"{node_id} {priority_label(display_priority(node_id))}"

        def node_size(node_id: str, has_trace: bool, _is_k8s_known: bool) -> int:
            if not has_trace:
                return 48
            return 68 if node_severity.get(node_id, "normal") != "normal" else 58

        nodes = build_runtime_nodes(
            graph,
            self.context.system_state.extra_attrs,
            style_for_node=node_style,
            label_for_node=node_label,
            size_for_node=node_size,
        )
        for node in nodes:
            if node["trace_status"] == "no_recent_trace":
                node["description"] = (
                    "服务已部署，但当前审计窗口内没有 Jaeger 调用边；"
                    "优先级仍可配置，但本次无法判断依赖强弱。"
                )
            else:
                node["description"] = (
                    f"优先级 {priority_label(display_priority(node['id']))}，"
                    "当前窗口内存在调用数据。"
                )

        severity_rank = {
            "strong_violation": 5,
            "weak_violation": 4,
            "missing_priority": 3,
            "strong": 2,
            "weak": 1,
            "normal": 0,
        }
        risky_edges.sort(
            key=lambda item: (
                -severity_rank.get(item["severity"], 0),
                -(item.get("correlation") or 0),
                item["source"],
                item["target"],
            )
        )
        top_edges = risky_edges[:5]

        def format_edge_text(item: dict) -> str:
            correlation = (
                f"{item['correlation']:.2f}"
                if item.get("correlation") is not None
                else "不足"
            )
            return (
                f"{item['source']}({priority_label(item['source_priority'])}) -> "
                f"{item['target']}({priority_label(item['target_priority'])})"
                f"（{item['dependency_type']}，相关性 {correlation}）"
            )

        if top_edges:
            edge_text = "；".join(format_edge_text(item) for item in top_edges)
        else:
            edge_text = "当前没有优先级约束违规或强弱依赖证据不足的边"

        strong_violations = [
            item for item in risky_edges if item["severity"] == "strong_violation"
        ]
        weak_violations = [
            item for item in risky_edges if item["severity"] == "weak_violation"
        ]
        missing_priority = [
            item for item in risky_edges if item["severity"] == "missing_priority"
        ]

        description = (
            "展示服务优先级与强弱依赖关系是否一致：P0 最高，数字越大优先级越低。"
            "红色边表示强依赖违规，即上游服务强依赖的下游优先级低于上游；"
            "橙色边表示弱依赖目标被配置得比依赖它的服务更高优先级；"
            "紫色边表示强依赖但优先级约束满足；蓝色边表示弱依赖。"
            "错误率、响应耗时和皮尔逊相关性只用于识别依赖强弱，不直接作为本问题的违规颜色。"
            "灰色虚线节点表示服务已部署，但当前审计窗口内没有 Jaeger 调用边。"
            f" 当前强依赖违规 {len(strong_violations)} 条，弱依赖优先级关注 {len(weak_violations)} 条，"
            f"缺少优先级配置 {len(missing_priority)} 条。Top 5：{edge_text}。"
        )

        legend = [
            {"color": "#ef4444", "label": "强依赖优先级违规"},
            {"color": "#f59e0b", "label": "弱依赖优先级关注"},
            {"color": "#8b5cf6", "label": "强依赖"},
            {"color": "#38bdf8", "label": "弱依赖"},
            {"color": "#94a3b8", "label": "缺少优先级配置"},
            {"color": "#cbd5e1", "label": "其他服务"},
            {"color": "#e2e8f0", "label": "无近期调用数据"},
        ]

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "依赖关系异常分析",
                "description": description,
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": legend,
            },
        )
