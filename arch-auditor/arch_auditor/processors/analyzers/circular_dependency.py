from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType


class CircularDependencyAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "CircularDependencyAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return []  # TODO: Add dependencies

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        simple_cycles = list(nx.simple_cycles(G))
        for cycle in simple_cycles:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Circular dependency detected: {' -> '.join(cycle)}",
                )
            )

            lowest_priority_node = cycle[0]

            for node in cycle:
                if G.nodes[node].get("priority", -1) < G.nodes[
                    lowest_priority_node
                ].get("priority", -1):
                    lowest_priority_node = node
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.INFO,
                    message=f"Suggested resolution: Review and refactor module '{lowest_priority_node}'(priority: {G.nodes[lowest_priority_node].get('priority', 'N/A')}) to break the cycle.",
                )
            )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
