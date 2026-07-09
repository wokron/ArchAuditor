from collections import defaultdict

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
        return False

    def visualize(self):
        pass
