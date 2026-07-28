import json
from pathlib import Path

import networkx as nx
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class SinglePointAnalyzer(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.dominator_tree = None
        self.criticality_scores = {}
        self.root = None

    @staticmethod
    def name() -> str:
        return "SinglePointAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["SingleRootDAGSource", "K8sConfigSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.warning_percentage_threshold = float(
            config.get("warning_percentage_threshold", 10.0)
        )
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        roots = [node for node in graph.nodes if graph.in_degree(node) == 0]
        if len(roots) != 1:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=(
                        "The service graph should have a single root, found "
                        f"{len(roots)} roots: {roots}"
                    ),
                )
            )
            self.context.system_state.extra_attrs["single_point_summary"] = {
                "root": None,
                "root_count": len(roots),
                "critical_nodes": [],
                "criticality_scores": {},
                "warning_percentage_threshold": self.warning_percentage_threshold,
                "dominator_tree": {},
            }
            return

        self.root = roots[0]
        self.dominator_tree = nx.immediate_dominators(graph, start=self.root)
        self._calculate_criticality()
        availability_risks = self._check_availability_guarantees()

        root_criticality = self.criticality_scores.get(self.root, 0)
        critical_nodes = []
        for node, score in self.criticality_scores.items():
            if node == self.root:
                continue
            percentage = (
                (score / root_criticality) * 100 if root_criticality > 0 else 0
            )
            if percentage > self.warning_percentage_threshold:
                critical_nodes.append(
                    {
                        "service": str(node),
                        "criticality_score": score,
                        "criticality_percentage": round(percentage, 2),
                        "dominator": str(self.dominator_tree.get(node)),
                    }
                )
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Service '{node}' is a critical single point of failure "
                            f"with criticality score {score} ({percentage:.2f}% of root's criticality)"
                        ),
                    )
                )

        critical_nodes.sort(
            key=lambda item: item["criticality_percentage"], reverse=True
        )
        self.context.system_state.extra_attrs["single_point_summary"] = {
            "root": str(self.root),
            "root_count": len(roots),
            "critical_nodes": critical_nodes,
            "availability_risk_nodes": availability_risks,
            "criticality_scores": {
                str(node): score for node, score in self.criticality_scores.items()
            },
            "warning_percentage_threshold": self.warning_percentage_threshold,
            "dominator_tree": {
                str(node): str(dom) for node, dom in (self.dominator_tree or {}).items()
            },
        }

    def _calculate_criticality(self):
        self.criticality_scores = {}
        assert self.dominator_tree is not None

        dominator_tree_children = {}
        for child, dom in self.dominator_tree.items():
            if child == dom:
                continue
            dominator_tree_children.setdefault(dom, []).append(child)

        def dfs(node):
            if node in self.criticality_scores:
                return self.criticality_scores[node]
            self_score = self._get_score(node)
            children_score = sum(
                dfs(child) for child in dominator_tree_children.get(node, [])
            )
            total_score = self_score + children_score
            self.criticality_scores[node] = total_score
            return total_score

        for node in self.context.system_state.graph.nodes:
            dfs(node)

    def _get_score(self, node) -> float:
        node_data = self.context.system_state.graph.nodes[node]
        return node_data.get("call_count", 0)

    def _check_availability_guarantees(self) -> list[dict]:
        graph = self.context.system_state.graph
        k8s = self.context.system_state.extra_attrs.get("k8s_configs", {}) or {}
        deployments = k8s.get("deployments", []) or []

        deployment_by_service = {}
        for dep in deployments:
            if not isinstance(dep, dict):
                continue
            service_names = {
                dep.get("name"),
                (dep.get("labels") or {}).get("app"),
                (dep.get("labels") or {}).get("app.kubernetes.io/name"),
                (dep.get("labels") or {}).get("service"),
                (dep.get("labels") or {}).get("service_name"),
                (dep.get("labels") or {}).get("app.kubernetes.io/component"),
            }
            for service_name in service_names:
                if service_name:
                    deployment_by_service.setdefault(service_name, dep)

        root_criticality = self.criticality_scores.get(self.root, 0)
        risks = []
        for node, score in sorted(
            self.criticality_scores.items(), key=lambda item: item[1], reverse=True
        ):
            if node == self.root or root_criticality <= 0:
                continue

            percentage = (score / root_criticality) * 100
            if percentage <= self.warning_percentage_threshold:
                continue

            dep = deployment_by_service.get(str(node))
            if not dep:
                continue

            replicas = int(dep.get("replicas") or 0)
            ready_replicas = int(dep.get("ready_replicas") or 0)
            missing_request_containers = []
            for container in dep.get("containers", []) or []:
                if not isinstance(container, dict):
                    continue
                requests = ((container.get("resources") or {}).get("requests") or {})
                if "cpu" not in requests or "memory" not in requests:
                    missing_request_containers.append(container.get("name", "unknown"))

            if replicas >= 2 and ready_replicas >= 2 and not missing_request_containers:
                continue

            risk = {
                "service": str(node),
                "namespace": dep.get("namespace"),
                "replicas": replicas,
                "ready_replicas": ready_replicas,
                "criticality_percentage": round(percentage, 2),
                "missing_request_containers": missing_request_containers,
                "has_replica_risk": replicas < 2 or ready_replicas < 2,
                "has_request_risk": bool(missing_request_containers),
            }
            risks.append(risk)

            reasons = []
            if risk["has_replica_risk"]:
                reasons.append(
                    f"replicas={replicas}, ready_replicas={ready_replicas}"
                )
            if risk["has_request_risk"]:
                reasons.append(
                    "missing requests on containers "
                    + ", ".join(missing_request_containers)
                )

            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=(
                        f"Critical service '{node}' lacks availability guarantees: "
                        + "; ".join(reasons)
                        + "."
                    ),
                )
            )

        return risks

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        if self.dominator_tree is None or not self.criticality_scores:
            return JSONResponse(
                {"error": "暂无可视化数据，请先运行审计。"}
            )

        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        graph = self.context.system_state.graph
        root = self.root
        if root is None:
            for node in graph.nodes:
                if graph.in_degree(node) == 0:
                    root = node
                    break
        if root is None:
            return JSONResponse({"error": "服务依赖图中未找到入口根节点。"})

        children_map = {}
        for child, dominator in self.dominator_tree.items():
            children_map.setdefault(dominator, []).append(child)

        max_criticality = (
            max(self.criticality_scores.values()) if self.criticality_scores else 1
        )

        def build_tree_node(node_id):
            criticality = self.criticality_scores.get(node_id, 0)
            normalized_criticality = (
                criticality / max_criticality if max_criticality > 0 else 0
            )
            percentage = normalized_criticality * 100

            if normalized_criticality > 0.5:
                color = "#ef4444"
            elif normalized_criticality > 0.2:
                color = "#f59e0b"
            elif normalized_criticality > 0.1:
                color = "#eab308"
            else:
                color = "#22c55e"

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

            children = children_map.get(node_id, [])
            if children:
                node["children"] = [build_tree_node(child) for child in children]
            return node

        tree_data = build_tree_node(root)

        root_criticality = self.criticality_scores.get(root, 0)
        critical_nodes = []
        for node, score in self.criticality_scores.items():
            if node == root:
                continue
            percentage = (score / root_criticality) * 100 if root_criticality > 0 else 0
            if percentage > self.warning_percentage_threshold:
                critical_nodes.append(f"{node} ({percentage:.1f}%)")

        description = (
            "支配树用于展示哪些服务一旦失败，会显著影响下游连通性和流量路径。"
        )
        if critical_nodes:
            description += (
                " 当前关键单点服务："
                + ", ".join(critical_nodes)
                + "。"
            )

        legend = [
            {"color": "#ef4444", "label": "极高关键性（> 50%）"},
            {"color": "#f59e0b", "label": "高关键性（20-50%）"},
            {"color": "#eab308", "label": "中等关键性（10-20%）"},
            {"color": "#22c55e", "label": "低关键性（< 10%）"},
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
