from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType


class SinglePointAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "SinglePointAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServicePrioritySource", "SingleRootDAGSource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        roots = []
        for node in G.nodes:
            if G.in_degree(node) == 0:
                roots.append(node)
        if len(roots) != 1:
            self.context.reporter.report(
                ReportMessage(
                    ReportType.ERROR,
                    f"The service graph should have a single root, found {len(roots)} roots: {roots}",
                )
            )
            return

        dominator_tree = nx.immediate_dominators(G, start=roots[0])
        for node, dominator in dominator_tree.items():
            if node == dominator:
                continue
            node_priority = self.context.system_state.graph.nodes[node].get(
                "priority", -1
            )
            dominator_priority = self.context.system_state.graph.nodes[dominator].get(
                "priority", -1
            )
            if dominator_priority >= node_priority:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.ERROR,
                        message=f"Service '{node}' has reversed priority due to its dominator '{dominator}': "
                        f"node priority = {node_priority}, dominator priority = {dominator_priority}",
                    )
                )

        # TODO: Use weighted sum to find most critical single points of failure

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
