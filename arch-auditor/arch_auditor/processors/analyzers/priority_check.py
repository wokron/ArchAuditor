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
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        for node in G.nodes:
            node_priority = G.nodes[node].get("priority", -1)
            predecessors = list(G.predecessors(node))
            all_weak = True
            pred_priorities = []
            for pred in predecessors:
                dep_type = G.edges[pred, node].get("dependency_type", "unknown")
                pred_priority = G.nodes[pred].get("priority", -1)
                pred_priorities.append(pred_priority)
                if dep_type == "strong" or dep_type == "unknown":
                    all_weak = False
                    if pred_priority < node_priority:
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.WARNING,
                                message=f"Priority violation: '{pred}' (priority: {pred_priority}) depends strongly on '{node}' (priority: {node_priority}).",
                            )
                        )
            if all_weak:
                lowest_pred = max(pred_priorities or [-1])
                if node_priority < lowest_pred:
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.INFO,
                            message=f"Consider reviewing '{node}' (priority: {node_priority}) as it is only weakly depended upon by services {predecessors}.",
                        )
                    )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
