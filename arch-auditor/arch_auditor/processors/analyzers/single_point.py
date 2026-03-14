from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import JSONResponse


class SinglePointAnalyzer(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.dominator_tree = None
        self.criticality_scores = {}

    @staticmethod
    def name() -> str:
        return "SinglePointAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return [
            "ServicePrioritySource",
            "SingleRootDAGSource",
            "PrometheusMetricsSource",
        ]

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
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"The service graph should have a single root, found {len(roots)} roots: {roots}",
                )
            )
            return

        root = roots[0]

        self.dominator_tree = nx.immediate_dominators(G, start=root)
        for node, dominator in self.dominator_tree.items():
            if node == dominator:
                continue
            node_priority = self.context.system_state.graph.nodes[node].get(
                "priority", -1
            )
            dominator_priority = self.context.system_state.graph.nodes[dominator].get(
                "priority", -1
            )
            if dominator_priority > node_priority:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.ERROR,
                        message=f"Service '{node}' has reversed priority due to its dominator '{dominator}': "
                        f"node priority = {node_priority}, dominator priority = {dominator_priority}",
                    )
                )

        self._calculate_criticality()

        root_criticality = self.criticality_scores.get(root, 0)
        for node, score in self.criticality_scores.items():
            if node == root:
                continue
            precentage = (score / root_criticality) * 100 if root_criticality > 0 else 0
            if (
                precentage > 10
            ):  # Arbitrary threshold for criticality # TODO: Make configurable
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=f"Service '{node}' is a critical single point of failure with criticality score {score} ({precentage:.2f}% of root's criticality)",
                    )
                )

    def _calculate_criticality(self):
        self.criticality_scores = {}
        assert self.dominator_tree is not None

        dominator_tree_children = {}
        for child, dom in self.dominator_tree.items():
            if child == dom:
                continue
            dominator_tree_children.setdefault(dom, []).append(child)

        def dfs(n):
            if n in self.criticality_scores:
                return self.criticality_scores[n]
            self_score = self._get_score(n)
            children_score = sum(
                dfs(child) for child in dominator_tree_children.get(n, [])
            )
            total_score = self_score + children_score
            self.criticality_scores[n] = total_score
            return total_score

        for node in self.context.system_state.graph.nodes:
            dfs(node)

    def _get_score(self, node) -> float:
        # Score is the avg of the node's qps
        node_data = self.context.system_state.graph.nodes[node]
        return node_data.get("call_count", 0)

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # TODO: Implement actual visualization of the dominator tree and criticality scores
        return JSONResponse(
            {
                "dominator_tree": self.dominator_tree,
                "criticality_scores": self.criticality_scores,
            }
        )
