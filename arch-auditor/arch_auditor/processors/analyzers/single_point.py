from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json


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
        if self.dominator_tree is None or not self.criticality_scores:
            return JSONResponse(
                {"error": "No data available. Please run the analyzer first."}
            )
        
        # Get templates directory
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        
        # Find root node - it's the node that appears in values but not in keys
        # Or we can get it from the graph (node with in_degree 0)
        G = self.context.system_state.graph
        root = None
        for node in G.nodes:
            if G.in_degree(node) == 0:
                root = node
                break
        
        if root is None:
            return JSONResponse({"error": "No root found in graph"})
        
        # Build children map from dominator tree
        children_map = {}
        for child, dominator in self.dominator_tree.items():
            children_map.setdefault(dominator, []).append(child)
        
        # Get the max criticality for normalization
        max_criticality = max(self.criticality_scores.values()) if self.criticality_scores else 1
        
        # Build tree structure recursively
        def build_tree_node(node_id):
            criticality = self.criticality_scores.get(node_id, 0)
            normalized_criticality = criticality / max_criticality if max_criticality > 0 else 0
            percentage = normalized_criticality * 100
            
            # Color based on criticality (red for high, yellow for medium, green for low)
            if normalized_criticality > 0.5:
                color = '#ef4444'  # Red
            elif normalized_criticality > 0.2:
                color = '#f59e0b'  # Orange
            elif normalized_criticality > 0.1:
                color = '#eab308'  # Yellow
            else:
                color = '#22c55e'  # Green
            
            node = {
                "id": str(node_id),
                "label": f"{percentage:.1f}%",
                "description": str(node_id),
                "name": str(node_id),
                "percentage": round(percentage, 1),
                "criticality": round(criticality, 2),
                "style": {
                    "fill": color,
                    "stroke": "#ffffff",
                    "lineWidth": 2,
                },
            }
            
            # Add priority if available
            G = self.context.system_state.graph
            if node_id in G.nodes:
                priority = G.nodes[node_id].get("priority", None)
                if priority is not None:
                    node["priority"] = priority
            
            # Add children
            children = children_map.get(node_id, [])
            if children:
                node["children"] = [build_tree_node(child) for child in children]
            
            return node
        
        tree_data = build_tree_node(root)
        
        # Calculate percentage for critical nodes
        root_criticality = self.criticality_scores.get(root, 0)
        critical_nodes = []
        for node, score in self.criticality_scores.items():
            if node != root:
                percentage = (score / root_criticality) * 100 if root_criticality > 0 else 0
                if percentage > 10:
                    critical_nodes.append(f"{node} ({percentage:.1f}%)")
        
        description = f"支配树展示了服务依赖关系中的关键控制点。节点大小和颜色表示其关键性分数（越大越红表示越关键）。"
        if critical_nodes:
            description += f" 关键单点故障服务：{', '.join(critical_nodes)}。"
        
        # Prepare legend
        legend = [
            {"color": "#ef4444", "label": f"极高关键性 (> 50% 根节点)"},
            {"color": "#f59e0b", "label": f"高关键性 (20-50%)"},
            {"color": "#eab308", "label": f"中关键性 (10-20%)"},
            {"color": "#22c55e", "label": f"低关键性 (< 10%)"},
        ]
        
        return templates.TemplateResponse(
            "tree_visualization.html",
            {
                "request": {},
                "title": "单点故障分析 - 支配树",
                "description": description,
                "tree_data": json.dumps(tree_data),
                "legend": legend,
            },
        )
