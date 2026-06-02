from ...processors import Processor
import networkx as nx
import requests
import time
from arch_auditor.reporter import ReportMessage, ReportType


class ServiceDependencySource(Processor):
    """Annotates graph edges with dependency_type (strong / weak / unknown).

    Supported source types:
      - Mock   – explicit dependency list in config
      - Jaeger – infers strong/weak from Jaeger call counts
    """

    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.jaeger_url: str | None = None

    @staticmethod
    def name() -> str:
        return "ServiceDependencySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type

        if source_type == "Mock":
            self.mock_deps = config.get("dependencies", [])
            return True
        elif source_type == "Jaeger":
            self.jaeger_url = config.get("jaeger_url", "")
            self.lookback_ms = config.get("lookback_ms", 3600000)
            self.strong_threshold = config.get("strong_call_threshold", 100)
            return bool(self.jaeger_url)
        else:
            return False

    def process(self) -> None:
        if self.source_type == "Mock":
            self._process_mock()
        elif self.source_type == "Jaeger":
            self._process_jaeger()

    def _process_mock(self) -> None:
        edge_attrs = {}
        for dep in self.mock_deps:
            from_service = dep.get("from")
            to_service = dep.get("to")
            dep_type = dep.get("type", "unknown")
            edge_attrs[(from_service, to_service)] = {"dependency_type": dep_type}
        nx.set_edge_attributes(self.context.system_state.graph, edge_attrs)

    def _process_jaeger(self) -> None:
        try:
            now_ts = int(time.time() * 1000)
            url = f"{self.jaeger_url}/api/dependencies?lookback={self.lookback_ms}&endTs={now_ts}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            dependencies = response.json()
            if "data" not in dependencies:
                return

            G = self.context.system_state.graph
            threshold = self.strong_threshold

            for dep in dependencies["data"]:
                parent = dep.get("parent")
                child = dep.get("child")
                call_count = dep.get("callCount", 0)
                if not parent or not child or parent == child:
                    continue
                if not G.has_edge(parent, child):
                    continue
                dep_type = "strong" if call_count >= threshold else "weak"
                G.edges[parent, child]["dependency_type"] = dep_type
                G.edges[parent, child]["call_count"] = call_count

            # Any remaining edge without an explicit type stays "unknown"
            for u, v in G.edges:
                if "dependency_type" not in G.edges[u, v]:
                    G.edges[u, v]["dependency_type"] = "unknown"

        except requests.exceptions.RequestException as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Failed to fetch Jaeger dependencies: {e}",
                )
            )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
