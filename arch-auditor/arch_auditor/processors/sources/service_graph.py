import json
import time
from pathlib import Path

import requests
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from arch_auditor.visualization_graph import build_runtime_nodes, trace_edge_label


class ServiceGraphSource(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.jaeger_url: str | None = None
        self.lookback_ms: int = 300000

    @staticmethod
    def name() -> str:
        return "ServiceGraphSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        config = config or {}
        source_type = config.get("type")
        if source_type is None:
            return False

        self.source_type = source_type
        if source_type == "Mock":
            edges = config.get("edges", [])
            self.context.system_state.graph.add_edges_from(edges)
            return True
        if source_type == "Jaeger":
            self.jaeger_url = config.get("jaeger_url")
            if not self.jaeger_url:
                return False
            self.lookback_ms = config.get("lookback_ms", 300000)
            return True
        return False

    def process(self) -> None:
        if self.source_type == "Jaeger":
            self._process_jaeger()

    def _process_jaeger(self) -> None:
        try:
            end_ts = int(time.time() * 1000)
            response = None
            for base_url in self._candidate_jaeger_urls():
                url = f"{base_url}/api/dependencies?lookback={self.lookback_ms}&endTs={end_ts}"
                candidate = requests.get(url, timeout=10)
                candidate.raise_for_status()
                if "application/json" in (candidate.headers.get("Content-Type", "")):
                    response = candidate
                    break

            if response is None:
                raise requests.exceptions.RequestException(
                    "Failed to locate Jaeger JSON dependencies endpoint"
                )

            dependencies = response.json()
            edges = []
            call_counts_by_target = {}

            for dep in dependencies.get("data", []):
                parent = dep.get("parent")
                child = dep.get("child")
                call_count = dep.get("callCount", 1)
                if parent and child and parent != child:
                    edges.append((parent, child))
                    call_counts_by_target[child] = (
                        call_counts_by_target.get(child, 0) + call_count
                    )

            if not edges:
                return

            self.context.system_state.graph.add_edges_from(edges)
            for node, count in call_counts_by_target.items():
                self.context.system_state.graph.nodes[node]["call_count"] = count

            for dep in dependencies.get("data", []):
                parent = dep.get("parent")
                child = dep.get("child")
                if parent and child and parent != child:
                    self.context.system_state.graph.edges[parent, child]["call_count"] = dep.get(
                        "callCount", 0
                    )

        except requests.exceptions.RequestException as exc:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Failed to fetch Jaeger dependencies: {exc}",
                )
            )
        except Exception as exc:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Unexpected error while processing Jaeger dependencies: {exc}",
                )
            )

    def _candidate_jaeger_urls(self) -> list[str]:
        base = (self.jaeger_url or "").rstrip("/")
        if base.endswith("/jaeger/ui"):
            return [base, base.removesuffix("/ui"), base.removesuffix("/jaeger/ui")]
        if base.endswith("/jaeger"):
            return [f"{base}/ui", base, base.removesuffix("/jaeger")]
        return [f"{base}/jaeger/ui", base]

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        graph = self.context.system_state.graph

        def node_style(_node_id: str, has_trace: bool, _is_k8s_known: bool) -> dict:
            if has_trace:
                return {
                    "fill": "#3b82f6",
                    "stroke": "#1d4ed8",
                    "lineWidth": 2,
                }
            return {
                "fill": "#e2e8f0",
                "stroke": "#94a3b8",
                "lineWidth": 1.5,
                "lineDash": [5, 4],
            }

        nodes = build_runtime_nodes(
            graph,
            self.context.system_state.extra_attrs,
            style_for_node=node_style,
        )

        edges = []
        for source, target, attrs in graph.edges(data=True):
            edge_data = {
                "source": str(source),
                "target": str(target),
            }
            label = trace_edge_label(attrs)
            if label:
                edge_data["label"] = label
            edges.append(edge_data)

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "服务依赖关系图",
                "description": (
                    "展示 Kubernetes 中已部署的服务节点，并叠加最近 Jaeger "
                    "窗口内观测到的调用边。灰色虚线节点表示服务已部署，"
                    "但当前审计窗口内没有采到调用关系。"
                ),
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": [
                    {"color": "#3b82f6", "label": "有近期调用数据"},
                    {"color": "#e2e8f0", "label": "无近期调用数据"},
                ],
            },
        )
