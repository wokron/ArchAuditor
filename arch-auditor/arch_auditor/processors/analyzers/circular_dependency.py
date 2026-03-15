from ...processors import Processor
import networkx as nx
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json


class CircularDependencyAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "CircularDependencyAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServicePrioritySource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        simple_cycles = nx.simple_cycles(G)
        # There might be self-loops, for example, tracing inside a service
        simple_cycles = filter(lambda c: len(c) > 1, simple_cycles)
        for cycle in simple_cycles:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Circular dependency detected: {' -> '.join(cycle + [cycle[0]])}",
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
        return True

    def visualize(self):
        # Get templates directory
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        
        # Convert NetworkX graph to G6 format
        G = self.context.system_state.graph
        
        # Find all cycles
        simple_cycles = list(nx.simple_cycles(G))
        simple_cycles = [c for c in simple_cycles if len(c) > 1]
        
        # Create sets for quick lookup
        nodes_in_cycles = set()
        edges_in_cycles = set()
        
        for cycle in simple_cycles:
            for i, node in enumerate(cycle):
                nodes_in_cycles.add(node)
                next_node = cycle[(i + 1) % len(cycle)]
                edges_in_cycles.add((node, next_node))
        
        nodes = []
        edges = []
        
        # Create nodes
        for node in G.nodes():
            node_data = {
                "id": str(node),
                "label": str(node),
            }
            
            # Highlight nodes in cycles
            if node in nodes_in_cycles:
                node_data["style"] = {
                    "fill": "#ef4444",  # Red for nodes in cycles
                    "stroke": "#991b1b",
                    "lineWidth": 3,
                }
                node_data["size"] = 70
            else:
                # Get priority for non-cycle nodes
                node_attrs = G.nodes[node]
                if "priority" in node_attrs:
                    priority = node_attrs["priority"]
                    if priority <= 1:
                        node_data["style"] = {"fill": "#1e40af"}
                    elif priority <= 3:
                        node_data["style"] = {"fill": "#3b82f6"}
                    else:
                        node_data["style"] = {"fill": "#93c5fd"}
            
            nodes.append(node_data)
        
        # Create edges
        for source, target in G.edges():
            edge_data = {
                "source": str(source),
                "target": str(target),
            }
            
            # Highlight edges in cycles
            if (source, target) in edges_in_cycles:
                edge_data["style"] = {
                    "stroke": "#ef4444",  # Red for edges in cycles
                    "lineWidth": 3,
                    "endArrow": {
                        "path": "M 0,0 L 10,4 L 10,-4 Z",
                        "fill": "#ef4444",
                    }
                }
                edge_data["label"] = "循环依赖"
            else:
                # Add edge attributes
                edge_attrs = G.edges[source, target]
                if "call_count" in edge_attrs:
                    edge_data["label"] = f"calls: {edge_attrs['call_count']}"
            
            edges.append(edge_data)
        
        graph_data = {
            "nodes": nodes,
            "edges": edges,
        }
        
        # Prepare description with cycle information
        cycle_descriptions = []
        for i, cycle in enumerate(simple_cycles, 1):
            cycle_str = " → ".join(cycle + [cycle[0]])
            cycle_descriptions.append(f"循环 {i}: {cycle_str}")
        
        description = "展示系统中的循环依赖关系。"
        if cycle_descriptions:
            description += f" 检测到 {len(simple_cycles)} 个循环依赖：" + "; ".join(cycle_descriptions)
        else:
            description += " 未检测到循环依赖。"
        
        # Prepare legend
        legend = [
            {"color": "#ef4444", "label": "循环依赖中的服务"},
            {"color": "#1e40af", "label": "其他高优先级服务"},
            {"color": "#3b82f6", "label": "其他中优先级服务"},
            {"color": "#93c5fd", "label": "其他低优先级服务"},
        ]
        
        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},  # Empty request object for compatibility
                "title": "循环依赖分析",
                "description": description,
                "graph_data": json.dumps(graph_data),
                "legend": legend,
            },
        )
