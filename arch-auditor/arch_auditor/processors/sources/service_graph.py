import json
import time
from pathlib import Path

import requests
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


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
        nodes = [
            {
                "id": str(node),
                "label": str(node),
                "style": {"fill": "#3b82f6"},
            }
            for node in graph.nodes()
        ]

        edges = []
        for source, target in graph.edges():
            edge_data = {
                "source": str(source),
                "target": str(target),
            }
            edge_attrs = graph.edges[source, target]
            if "call_count" in edge_attrs:
                edge_data["label"] = f"calls: {edge_attrs['call_count']}"
            edges.append(edge_data)

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "Service Dependency Graph",
                "description": "Display dependency relationships between services.",
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": [{"color": "#3b82f6", "label": "service node"}],
            },
        )
