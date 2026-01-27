from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType


class OverDecompositionAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "OverDecompositionAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["SingleRootDAGSource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph

        roots = []
        for n in G.nodes:
            if G.in_degree(n) == 0:
                roots.append(n)
        if len(roots) > 1:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=f"Multiple root services detected ({len(roots)} roots). This may indicate over-decomposition. Consider consolidating services.",
                )
            )

        longest_path = nx.dag_longest_path(G)
        longest_path_length = len(longest_path) - 1
        # Example threshold for path length TODO: Make configurable
        threshold_path_length = 5
        if longest_path_length > threshold_path_length:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=f"Long dependency chain detected: Longest path is {longest_path} (length {longest_path_length}), which exceeds the threshold of {threshold_path_length}. Consider simplifying the service interactions.",
                )
            )

        pipe_services = []
        for n in G.nodes:
            in_degree = G.in_degree(n)
            out_degree = G.out_degree(n)
            if (in_degree == 1 and out_degree == 1) or in_degree + out_degree == 1:
                pipe_services.append(n)
        # Example threshold TODO: Make configurable
        threshold_pipe_service_ratio = 0.3
        if len(pipe_services) / G.number_of_nodes() > threshold_pipe_service_ratio:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=f"High proportion of pipe services detected ({len(pipe_services)} pipe services). This may indicate over-decomposition. Consider reviewing these services: {pipe_services}",
                )
            )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
