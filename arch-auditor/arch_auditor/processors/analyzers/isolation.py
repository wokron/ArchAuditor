from collections import defaultdict

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class IsolationAnalyzer(Processor):
    """Check whether critical services lack physical isolation."""

    @staticmethod
    def name() -> str:
        return "IsolationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "ServicePrioritySource"]

    def init(self, config) -> bool:
        config = config or {}
        self.critical_priority_threshold = config.get(
            "critical_priority_threshold", 0
        )
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        explicit_priorities = (
            self.context.system_state.extra_attrs.get("service_priorities", {}) or {}
        )
        k8s = self.context.system_state.extra_attrs.get("k8s_configs", {}) or {}
        node_details = k8s.get("node_details", {}) or {}
        pods = k8s.get("pods", []) or []

        summary = {
            "critical_priority_threshold": self.critical_priority_threshold,
            "critical_services": [],
            "co_located_service_groups": [],
            "single_zone_services": [],
            "placements": [],
            "service_zone_spread": [],
            "node_details": node_details,
        }

        p0_services = self._collect_critical_services(graph, explicit_priorities)
        summary["critical_services"] = sorted(p0_services)
        if not p0_services:
            self.context.system_state.extra_attrs["isolation_summary"] = summary
            return

        placements = []
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            svc = self._resolve_service_name(pod)
            if svc not in p0_services:
                continue
            node = pod.get("node_name", "") or ""
            zone = pod.get("zone", "") or ""
            if not node:
                continue
            placements.append((svc, node, zone, pod))
            summary["placements"].append(
                {
                    "service": svc,
                    "pod": pod.get("name"),
                    "namespace": pod.get("namespace"),
                    "node": node,
                    "zone": zone,
                    "labels": dict(pod.get("labels") or {}),
                }
            )

        node_services: dict[str, set[str]] = defaultdict(set)
        for svc, node, _zone, _pod in placements:
            node_services[node].add(svc)

        for node, svcs in sorted(node_services.items()):
            if len(svcs) <= 1:
                continue
            item = {
                "node": node,
                "services": sorted(svcs),
                "zone": (node_details.get(node) or {}).get("zone"),
            }
            summary["co_located_service_groups"].append(item)
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=(
                        f"P0 services {sorted(svcs)} co-located on node '{node}'. "
                        "These critical services lack physical isolation."
                    ),
                )
            )

        service_nodes: dict[str, set[str]] = defaultdict(set)
        service_zones: dict[str, set[str]] = defaultdict(set)
        for svc, node, zone, _pod in placements:
            service_nodes[svc].add(node)
            if zone:
                service_zones[svc].add(zone)

        for svc in sorted(p0_services):
            zones = service_zones.get(svc, set())
            nodes = service_nodes.get(svc, set())
            summary["service_zone_spread"].append(
                {
                    "service": svc,
                    "replica_nodes": sorted(nodes),
                    "replica_zones": sorted(zones),
                    "replica_node_count": len(nodes),
                    "replica_zone_count": len(zones),
                }
            )
            if zones and len(zones) == 1:
                summary["single_zone_services"].append(
                    {
                        "service": svc,
                        "zones": sorted(zones),
                        "replica_nodes": sorted(nodes),
                    }
                )
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"P0 service '{svc}' has all its replicas in a single "
                            f"availability zone '{next(iter(zones))}'. "
                            "Consider spreading pods across multiple zones."
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

    def _collect_critical_services(
        self, graph, explicit_priorities: dict[str, int]
    ) -> set[str]:
        critical = set()
        for service, priority in explicit_priorities.items():
            try:
                if int(priority) <= self.critical_priority_threshold:
                    critical.add(service)
            except (TypeError, ValueError):
                continue

        for node in graph.nodes:
            if node not in explicit_priorities:
                continue
            priority = explicit_priorities.get(node)
            try:
                if int(priority) <= self.critical_priority_threshold:
                    critical.add(node)
            except (TypeError, ValueError):
                continue
        return critical

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
