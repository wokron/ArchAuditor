import json
from pathlib import Path

import networkx as nx
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class CircularDependencyAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "CircularDependencyAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        cycles = [cycle for cycle in nx.simple_cycles(graph) if len(cycle) > 1]
        summary_cycles = []

        for cycle in cycles:
            cycle_path = " -> ".join(str(node) for node in (cycle + [cycle[0]]))
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Circular dependency detected: {cycle_path}",
                )
            )

            suggested_node = self._select_refactor_candidate(graph, cycle)
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.INFO,
                    message=(
                        "Suggested resolution: Review and refactor module "
                        f"'{suggested_node}' to break the cycle."
                    ),
                )
            )

            summary_cycles.append(
                {
                    "services": [str(node) for node in cycle],
                    "cycle_path": cycle_path,
                    "suggested_refactor_service": str(suggested_node),
                }
            )

        self.context.system_state.extra_attrs["circular_dependency_summary"] = {
            "count": len(summary_cycles),
            "cycles": summary_cycles,
        }

    @staticmethod
    def _select_refactor_candidate(graph, cycle: list) -> str:
        def candidate_key(node):
            total_degree = graph.in_degree(node) + graph.out_degree(node)
            return (total_degree, str(node))

        return min(cycle, key=candidate_key)

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        graph = self.context.system_state.graph
        cycles = [cycle for cycle in nx.simple_cycles(graph) if len(cycle) > 1]

        nodes_in_cycles = set()
        edges_in_cycles = set()
        for cycle in cycles:
            for index, node in enumerate(cycle):
                nodes_in_cycles.add(node)
                next_node = cycle[(index + 1) % len(cycle)]
                edges_in_cycles.add((node, next_node))

        nodes = []
        for node in graph.nodes():
            node_data = {
                "id": str(node),
                "label": str(node),
            }
            if node in nodes_in_cycles:
                node_data["style"] = {
                    "fill": "#ef4444",
                    "stroke": "#991b1b",
                    "lineWidth": 3,
                }
                node_data["size"] = 70
            else:
                node_data["style"] = {
                    "fill": "#cbd5e1",
                    "stroke": "#64748b",
                    "lineWidth": 1.5,
                }
            nodes.append(node_data)

        edges = []
        for source, target in graph.edges():
            edge_data = {
                "source": str(source),
                "target": str(target),
            }
            if (source, target) in edges_in_cycles:
                edge_data["style"] = {
                    "stroke": "#ef4444",
                    "lineWidth": 3,
                    "endArrow": {
                        "path": "M 0,0 L 10,4 L 10,-4 Z",
                        "fill": "#ef4444",
                    },
                }
                edge_data["label"] = "cycle"
            else:
                edge_attrs = graph.edges[source, target]
                if "call_count" in edge_attrs:
                    edge_data["label"] = f"calls: {edge_attrs['call_count']}"
            edges.append(edge_data)

        cycle_descriptions = []
        for index, cycle in enumerate(cycles, start=1):
            cycle_path = " -> ".join(str(node) for node in (cycle + [cycle[0]]))
            cycle_descriptions.append(f"Cycle {index}: {cycle_path}")

        description = "Display circular dependencies detected in the service graph."
        if cycle_descriptions:
            description += " " + "; ".join(cycle_descriptions)
        else:
            description += " No circular dependencies detected."

        legend = [
            {"color": "#ef4444", "label": "services in a cycle"},
            {"color": "#cbd5e1", "label": "other services"},
        ]

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "Circular Dependency Analysis",
                "description": description,
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": legend,
            },
        )
