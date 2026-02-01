from ...processors import Processor
from abc import ABC, abstractmethod
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import HTMLResponse


class ServiceGraphSource(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.jaeger_url: str | None = None

    @staticmethod
    def name() -> str:
        return "ServiceGraphSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        type = config.get("type", None)
        if type is None:
            return False

        self.source_type = type

        if type == "Mock":
            edges = config.get("edges", [])
            self.context.system_state.graph.add_edges_from(edges)
            return True
        elif type == "Jaeger":
            jaeger_url = config.get("jaeger_url", None)
            if jaeger_url is None:
                return False
            self.jaeger_url = jaeger_url
            return True
        else:
            # Unknown type
            return False

    def process(self) -> None:
        if self.source_type == "Jaeger":
            self._process_jaeger()

    def _process_jaeger(self) -> None:
        try:
            url = f"{self.jaeger_url}/api/dependencies"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            dependencies = response.json()
            edges = []

            # Jaeger dependencies API format:
            # {"data": [{"parent": "service1", "child": "service2", "callCount": 123}, ...]}
            if "data" in dependencies:
                for dep in dependencies["data"]:
                    parent = dep.get("parent")
                    child = dep.get("child")
                    if parent and child:
                        edges.append((parent, child))

            # Update graph structure
            if edges:
                self.context.system_state.graph.add_edges_from(edges)

        except requests.exceptions.RequestException as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Failed to fetch Jaeger dependencies: {e}",
                )
            )
        except Exception as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Unexpected error while processing Jaeger dependencies: {e}",
                )
            )

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # TODO: Implement actual visualization logic
        graph_text = str(self.context.system_state.graph)
        html_content = f"""<html>
        <body>
            <h1>Service Graph</h1>
            <pre>{graph_text}</pre>
        </body>
        </html>"""
        return HTMLResponse(content=html_content)
