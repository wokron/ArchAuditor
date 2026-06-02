from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from collections import defaultdict


class IsolationAnalyzer(Processor):
    """Check whether critical (P0) services lack physical isolation.

    Rules:
      - Two different P0 services sharing the same node  ->  ERROR
      - All replicas of a P0 service in a single zone     ->  WARNING
    """

    @staticmethod
    def name() -> str:
        return "IsolationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "ServicePrioritySource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph

        # ---- collect P0 service names ----
        p0_services: set[str] = set()
        for node in G.nodes:
            if G.nodes[node].get("priority", -1) <= 1:
                p0_services.add(node)

        if not p0_services:
            return

        # ---- read pod placement from K8s source ----
        k8s = self.context.system_state.extra_attrs.get("k8s_configs", {})
        pods = k8s.get("pods", [])

        # pod -> (service_name, node_name, zone)
        # We use labels to guess the owning service (e.g. app=svc-a).
        placements: list[tuple[str, str, str]] = []  # (service, node, zone)

        for pod in pods:
            if not isinstance(pod, dict):
                continue
            labels = pod.get("labels", {})
            svc = (
                labels.get("app")
                or labels.get("service")
                or labels.get("service_name")
            )
            if svc not in p0_services:
                continue
            node = pod.get("node_name", "")
            zone = pod.get("zone", "")
            if node:
                placements.append((svc, node, zone))

        # ---- Rule 1: different P0 services on the same node ----
        node_services: dict[str, set[str]] = defaultdict(set)
        for svc, node, _ in placements:
            node_services[node].add(svc)

        for node, svcs in node_services.items():
            if len(svcs) > 1:
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

        # ---- Rule 2: all replicas of a P0 service in single zone ----
        svc_zones: dict[str, set[str]] = defaultdict(set)
        for svc, _, zone in placements:
            if zone:
                svc_zones[svc].add(zone)

        for svc in p0_services:
            zones = svc_zones.get(svc)
            if zones and len(zones) == 1:
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

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
