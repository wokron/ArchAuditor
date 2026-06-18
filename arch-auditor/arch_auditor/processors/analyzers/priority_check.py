from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType


class PriorityCheckAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "PriorityCheckAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServicePrioritySource", "ServiceDependencySource"]

    def init(self, config) -> bool:
        self.min_core_indegree = int(config.get("min_core_indegree", 2))
        self.max_edge_indegree = int(config.get("max_edge_indegree", 1))
        self.high_pagerank_percentile = float(
            config.get("high_pagerank_percentile", 0.75)
        )
        self.low_pagerank_percentile = float(
            config.get("low_pagerank_percentile", 0.25)
        )
        self.min_pagerank_ratio = float(config.get("min_pagerank_ratio", 1.2))
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        if G.number_of_nodes() == 0 or G.number_of_edges() == 0:
            return

        pagerank_scores = self._dependency_centrality(G)
        indegrees = dict(G.in_degree())
        high_pagerank_threshold = self._percentile_threshold(
            pagerank_scores.values(), self.high_pagerank_percentile
        )
        low_pagerank_threshold = self._percentile_threshold(
            pagerank_scores.values(), self.low_pagerank_percentile
        )

        for source, target, edge_data in G.edges(data=True):
            dep_type = edge_data.get("dependency_type", "unknown")
            if dep_type != "strong":
                continue

            source_pagerank = pagerank_scores.get(source, 0.0)
            target_pagerank = pagerank_scores.get(target, 0.0)
            source_indegree = indegrees.get(source, 0)
            target_indegree = indegrees.get(target, 0)

            is_core_source = (
                source_indegree >= self.min_core_indegree
                and source_pagerank >= high_pagerank_threshold
            )
            is_edge_target = (
                target_indegree <= self.max_edge_indegree
                and target_pagerank <= low_pagerank_threshold
            )
            pagerank_ratio = self._safe_ratio(source_pagerank, target_pagerank)

            if not is_core_source or not is_edge_target:
                continue
            if source_indegree < target_indegree:
                continue
            if pagerank_ratio < self.min_pagerank_ratio:
                continue

            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        "Dependency hierarchy violation: "
                        f"core service '{source}' "
                        f"(pagerank: {source_pagerank:.4f}, indegree: {source_indegree}) "
                        f"depends strongly on edge service '{target}' "
                        f"(pagerank: {target_pagerank:.4f}, indegree: {target_indegree})."
                    ),
                )
            )

    @staticmethod
    def _dependency_centrality(graph: nx.DiGraph) -> dict[str, float]:
        # Reverse the call graph so "being depended on by many services"
        # increases centrality instead of rewarding sink nodes.
        centrality_graph = graph.reverse(copy=True)
        return nx.pagerank(centrality_graph)

    @staticmethod
    def _percentile_threshold(values, percentile: float) -> float:
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return 0.0
        if len(ordered) == 1:
            return ordered[0]

        percentile = min(max(percentile, 0.0), 1.0)
        position = percentile * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float:
        if denominator <= 0:
            return float("inf") if numerator > 0 else 1.0
        return numerator / denominator

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
