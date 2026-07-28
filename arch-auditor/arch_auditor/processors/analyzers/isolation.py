from collections import defaultdict
import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class IsolationAnalyzer(Processor):
    """Check whether service replicas lack physical isolation."""

    @staticmethod
    def name() -> str:
        return "IsolationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.min_replicas_for_spread = int(config.get("min_replicas_for_spread", 2))
        return True

    def process(self) -> None:
        k8s = self.context.system_state.extra_attrs.get("k8s_configs", {}) or {}
        node_details = k8s.get("node_details", {}) or {}
        pods = k8s.get("pods", []) or []

        summary = {
            "analyzed_services": [],
            "min_replicas_for_spread": self.min_replicas_for_spread,
            "co_located_service_groups": [],
            "same_node_replica_services": [],
            "single_zone_services": [],
            "placements": [],
            "service_zone_spread": [],
            "node_details": node_details,
        }

        placements_by_service: dict[str, list[dict]] = defaultdict(list)
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            service = self._resolve_service_name(pod)
            node = pod.get("node_name", "") or ""
            zone = pod.get("zone", "") or ""
            if not service or not node:
                continue
            placement = {
                "service": service,
                "pod": pod.get("name"),
                "namespace": pod.get("namespace"),
                "node": node,
                "zone": zone,
                "labels": dict(pod.get("labels") or {}),
            }
            placements_by_service[service].append(placement)
            summary["placements"].append(placement)

        for service in sorted(placements_by_service):
            placements = placements_by_service[service]
            replica_count = len(placements)
            if replica_count < self.min_replicas_for_spread:
                continue

            summary["analyzed_services"].append(service)
            nodes = sorted({item["node"] for item in placements if item.get("node")})
            zones = sorted({item["zone"] for item in placements if item.get("zone")})
            summary["service_zone_spread"].append(
                {
                    "service": service,
                    "replica_pod_count": replica_count,
                    "replica_nodes": nodes,
                    "replica_zones": zones,
                    "replica_node_count": len(nodes),
                    "replica_zone_count": len(zones),
                }
            )

            pods_by_node: dict[str, list[str]] = defaultdict(list)
            for placement in placements:
                node = placement.get("node")
                pod = placement.get("pod")
                if node and pod:
                    pods_by_node[node].append(pod)

            for node, node_pods in sorted(pods_by_node.items()):
                if len(node_pods) < 2:
                    continue
                item = {
                    "service": service,
                    "node": node,
                    "zone": (node_details.get(node) or {}).get("zone"),
                    "pods": sorted(node_pods),
                    "replica_count_on_node": len(node_pods),
                    "total_replica_count": replica_count,
                    "all_replicas_on_same_node": len(nodes) == 1,
                }
                summary["same_node_replica_services"].append(item)
                summary["co_located_service_groups"].append(
                    {
                        "node": node,
                        "services": [service],
                        "zone": item["zone"],
                        "pods": item["pods"],
                        "replica_count": len(node_pods),
                    }
                )
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=(
                            ReportType.ERROR
                            if len(nodes) == 1
                            else ReportType.WARNING
                        ),
                        message=(
                            f"Service '{service}' has {len(node_pods)} replicas on node '{node}'. "
                            "Replica placement is not sufficiently isolated."
                        ),
                    )
                )

            if len(nodes) == 1:
                pass

            if zones and len(zones) == 1:
                summary["single_zone_services"].append(
                    {
                        "service": service,
                        "zones": zones,
                        "replica_nodes": nodes,
                        "replica_count": replica_count,
                    }
                )
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Service '{service}' has all {replica_count} replicas in a single "
                            f"availability zone '{zones[0]}'. Consider spreading replicas across multiple zones."
                        ),
                    )
                )

        summary["placements"].sort(
            key=lambda item: (
                item.get("service") or "",
                item.get("namespace") or "",
                item.get("pod") or "",
            )
        )
        self.context.system_state.extra_attrs["isolation_summary"] = summary

    @staticmethod
    def _resolve_service_name(pod: dict) -> str | None:
        labels = pod.get("labels") or {}
        for key in (
            "app",
            "app.kubernetes.io/name",
            "service",
            "service_name",
            "app.kubernetes.io/component",
        ):
            value = labels.get(key)
            if value:
                return value

        pod_name = pod.get("name") or ""
        if "-" in pod_name:
            return pod_name.rsplit("-", 2)[0]
        return pod_name or None

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        summary = (
            self.context.system_state.extra_attrs.get("isolation_summary", {}) or {}
        )
        placements = [
            item
            for item in summary.get("placements", []) or []
            if isinstance(item, dict)
        ]
        node_details = summary.get("node_details", {}) or {}
        same_node_items = [
            item
            for item in summary.get("same_node_replica_services", []) or []
            if isinstance(item, dict)
        ]
        single_zone_items = [
            item
            for item in summary.get("single_zone_services", []) or []
            if isinstance(item, dict)
        ]
        colocated_items = [
            item
            for item in summary.get("co_located_service_groups", []) or []
            if isinstance(item, dict)
        ]

        same_node_services = {
            item.get("service") for item in same_node_items if item.get("service")
        }
        single_zone_services = {
            item.get("service") for item in single_zone_items if item.get("service")
        }
        colocated_services = {
            service
            for item in colocated_items
            for service in item.get("services", []) or []
            if service
        }
        risk_services = same_node_services | single_zone_services | colocated_services
        risk_nodes = {
            item.get("node")
            for item in same_node_items + colocated_items
            if item.get("node")
        }
        risk_zones = {
            zone
            for item in single_zone_items
            for zone in item.get("zones", []) or []
            if zone
        }

        node_zones: dict[str, str] = {}
        for node, details in node_details.items():
            if not node:
                continue
            zone = ""
            if isinstance(details, dict):
                zone = details.get("zone") or ""
            node_zones[str(node)] = str(zone or "unknown-zone")
        for placement in placements:
            node = placement.get("node")
            if not node:
                continue
            zone = placement.get("zone") or node_zones.get(str(node)) or "unknown-zone"
            node_zones[str(node)] = str(zone)

        analyzed_services = {
            service
            for service in summary.get("analyzed_services", []) or []
            if service
        }
        display_services = risk_services or set(sorted(analyzed_services)[:8])
        selected_placements = [
            item for item in placements if item.get("service") in display_services
        ]
        if not selected_placements:
            selected_placements = placements[:18]

        display_nodes = {
            str(item.get("node"))
            for item in selected_placements
            if item.get("node")
        } | risk_nodes
        if len(node_zones) <= 10:
            display_nodes |= set(node_zones)

        display_zones = sorted(
            {node_zones.get(node, "unknown-zone") for node in display_nodes}
        )

        nodes = []
        edges = []
        added_nodes = set()
        added_edges = set()

        def add_node(node_id: str, label: str, style: dict, size: int) -> None:
            if node_id in added_nodes:
                return
            added_nodes.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "label": label,
                    "style": style,
                    "size": size,
                }
            )

        def add_edge(source: str, target: str, label: str | None, style: dict) -> None:
            edge_key = (source, target, label or "")
            if edge_key in added_edges:
                return
            added_edges.add(edge_key)
            edge = {
                "source": source,
                "target": target,
                "style": style,
            }
            if label:
                edge["label"] = label
            edges.append(edge)

        for zone in display_zones:
            is_risk_zone = zone in risk_zones
            add_node(
                f"zone:{zone}",
                zone,
                {
                    "fill": "#f59e0b" if is_risk_zone else "#bfdbfe",
                    "stroke": "#b45309" if is_risk_zone else "#2563eb",
                    "lineWidth": 3 if is_risk_zone else 2,
                },
                86,
            )

        for node in sorted(display_nodes):
            zone = node_zones.get(node, "unknown-zone")
            is_risk_node = node in risk_nodes
            add_node(
                f"node:{node}",
                node,
                {
                    "fill": "#ef4444" if is_risk_node else "#60a5fa",
                    "stroke": "#991b1b" if is_risk_node else "#1d4ed8",
                    "lineWidth": 3 if is_risk_node else 2,
                },
                68 if is_risk_node else 62,
            )
            add_edge(
                f"zone:{zone}",
                f"node:{node}",
                None,
                {
                    "stroke": "#94a3b8",
                    "lineWidth": 2,
                    "endArrow": {
                        "path": "M 0,0 L 10,4 L 10,-4 Z",
                        "fill": "#94a3b8",
                    },
                },
            )

        service_seen_count: dict[str, int] = defaultdict(int)
        for placement in selected_placements:
            service = placement.get("service") or "unknown-service"
            pod = placement.get("pod") or service
            namespace = placement.get("namespace") or "default"
            node = placement.get("node")
            if not node:
                continue
            service_seen_count[service] += 1
            pod_id = f"pod:{namespace}:{pod}:{service_seen_count[service]}"
            is_same_node = service in same_node_services and node in risk_nodes
            is_single_zone = service in single_zone_services
            is_risk_pod = is_same_node or is_single_zone
            add_node(
                pod_id,
                f"{service} #{service_seen_count[service]}",
                {
                    "fill": (
                        "#ef4444"
                        if is_same_node
                        else "#f59e0b"
                        if is_single_zone
                        else "#22c55e"
                    ),
                    "stroke": (
                        "#991b1b"
                        if is_same_node
                        else "#b45309"
                        if is_single_zone
                        else "#15803d"
                    ),
                    "lineWidth": 3 if is_risk_pod else 2,
                },
                56 if is_risk_pod else 48,
            )
            add_edge(
                f"node:{node}",
                pod_id,
                "same-node" if is_same_node else "single-zone" if is_single_zone else "replica",
                {
                    "stroke": "#ef4444" if is_same_node else "#f59e0b" if is_single_zone else "#94a3b8",
                    "lineWidth": 3 if is_risk_pod else 2,
                    "endArrow": {
                        "path": "M 0,0 L 10,4 L 10,-4 Z",
                        "fill": "#ef4444" if is_same_node else "#f59e0b" if is_single_zone else "#94a3b8",
                    },
                },
            )

        issue_count = len(same_node_items) + len(single_zone_items) + len(colocated_items)
        risk_service_text = ", ".join(sorted(risk_services)) or "暂无"
        description = (
            "展示 Kubernetes 副本在可用区、节点和 Pod 三层的放置关系。"
            "红色表示同一服务多个副本落在同一节点，橙色表示服务副本集中在单个可用区。"
            f" 当前隔离风险 {issue_count} 项，风险服务：{risk_service_text}。"
        )
        if same_node_items:
            examples = []
            for item in same_node_items[:3]:
                examples.append(
                    f"{item.get('service')} 在 {item.get('node')} 上有 "
                    f"{item.get('replica_count_on_node')}/{item.get('total_replica_count')} 个副本"
                )
            description += " " + "；".join(examples) + "。"

        legend = [
            {"color": "#bfdbfe", "label": "可用区"},
            {"color": "#60a5fa", "label": "普通节点"},
            {"color": "#ef4444", "label": "同节点风险"},
            {"color": "#f59e0b", "label": "单可用区风险"},
            {"color": "#22c55e", "label": "服务副本"},
        ]

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "物理隔离分析",
                "description": description,
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": legend,
            },
        )
