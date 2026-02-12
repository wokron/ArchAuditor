from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class SingleRootDAGSource(Processor):
    @staticmethod
    def name() -> str:
        return "SingleRootDAGSource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        roots = [n for n in G.nodes if G.in_degree(n) == 0]
        if len(roots) == 0:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message="The system has no root services (services with no dependencies).",
                )
            )
            return

        if len(roots) == 1:
            return  # Already a single root

        source_node = "<SOURCE>"
        if source_node in roots:
            for root in roots:
                if root != source_node:
                    G.add_edge(source_node, root)
        else:
            G.add_node(source_node)
            for root in roots:
                G.add_edge(source_node, root)

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
