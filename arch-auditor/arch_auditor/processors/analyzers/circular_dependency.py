import json
from pathlib import Path

import networkx as nx
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from arch_auditor.visualization_graph import build_runtime_nodes, trace_edge_label


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

        cycle_node_ids = {str(node) for node in nodes_in_cycles}

        def node_style(node_id: str, has_trace: bool, _is_k8s_known: bool) -> dict:
            if node_id in cycle_node_ids:
                return {
                    "fill": "#ef4444",
                    "stroke": "#991b1b",
                    "lineWidth": 3,
                }
            if has_trace:
                return {
                    "fill": "#cbd5e1",
                    "stroke": "#64748b",
                    "lineWidth": 1.5,
                }
            return {
                "fill": "#e2e8f0",
                "stroke": "#94a3b8",
                "lineWidth": 1.5,
                "lineDash": [5, 4],
            }

        def node_size(node_id: str, _has_trace: bool, _is_k8s_known: bool) -> int:
            return 70 if node_id in cycle_node_ids else 56

        nodes = build_runtime_nodes(
            graph,
            self.context.system_state.extra_attrs,
            style_for_node=node_style,
            size_for_node=node_size,
        )

        edges = []
        for source, target, attrs in graph.edges(data=True):
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
                label = trace_edge_label(attrs)
                if label:
                    edge_data["label"] = label
            edges.append(edge_data)

        cycle_descriptions = []
        for index, cycle in enumerate(cycles, start=1):
            cycle_path = " -> ".join(str(node) for node in (cycle + [cycle[0]]))
            cycle_descriptions.append(f"环路 {index}: {cycle_path}")

        description = (
            "展示 Kubernetes 中已部署的服务节点，并在最近 Jaeger 调用图中高亮"
            "检测到的循环依赖。灰色虚线节点表示当前审计窗口内没有调用边。"
        )
        if cycle_descriptions:
            description += " " + "; ".join(cycle_descriptions)
        else:
            description += " 当前未检测到循环依赖。"

        legend = [
            {"color": "#ef4444", "label": "环路中的服务"},
            {"color": "#cbd5e1", "label": "其他服务"},
            {"color": "#e2e8f0", "label": "无近期调用数据"},
        ]

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "循环依赖分析",
                "description": description,
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": legend,
            },
        )
