import datetime
from collections import defaultdict

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class MaintainabilityAnalyzer(Processor):
    """Assess whether services are becoming harder to maintain.

    Rules:
      - Pod start-up time (creation → ready) > 30 s  →  WARNING
      - High rollback ratio (rollbacks / total deploys) > threshold  →  WARNING
      - Two services always deployed together  →  INFO (tight coupling)
    """

    @staticmethod
    def name() -> str:
        return "MaintainabilityAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "DeploymentHistorySource"]

    def init(self, config) -> bool:
        self.startup_threshold_seconds = config.get("startup_threshold_seconds", 30)
        self.rollback_ratio_threshold = config.get("rollback_ratio_threshold", 0.25)
        self.co_deploy_overlap_threshold = config.get(
            "co_deploy_overlap_threshold", 0.8
        )
        return True

    def process(self) -> None:
        self._check_startup_time()
        self._check_rollback_rate()
        self._check_co_deployment()

    # ------------------------------------------------------------------
    # Rule 1 – slow pod startup
    # ------------------------------------------------------------------
    def _check_startup_time(self) -> None:
        k8s = self.context.system_state.extra_attrs.get("k8s_configs", {})
        pods = k8s.get("pods", []) or []
        threshold = self.startup_threshold_seconds

        for pod in pods:
            if not isinstance(pod, dict):
                continue
            created = pod.get("creation_time", "")
            ready = pod.get("ready_time", "")
            if not created or not ready:
                continue
            try:
                t_create = datetime.datetime.fromisoformat(created)
                t_ready = datetime.datetime.fromisoformat(ready)
            except ValueError:
                continue
            duration = (t_ready - t_create).total_seconds()
            if duration > threshold:
                svc = (pod.get("labels") or {}).get(
                    "app"
                ) or pod.get("name", "unknown")
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Service '{svc}' pod '{pod.get('name', 'unknown')}' "
                            f"took {duration:.0f}s to become ready "
                            f"(threshold: {threshold}s). "
                            "Start-up logic may be too complex or have too many dependencies."
                        ),
                    )
                )

    # ------------------------------------------------------------------
    # Rule 2 – high rollback ratio
    # ------------------------------------------------------------------
    def _check_rollback_rate(self) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        if not events:
            return

        svc_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"deploys": 0, "rollbacks": 0}
        )
        for ev in events:
            if not isinstance(ev, dict):
                continue
            svc = ev.get("service", "")
            action = (ev.get("action") or "").lower()
            if action == "deploy":
                svc_stats[svc]["deploys"] += 1
            elif action == "rollback":
                svc_stats[svc]["rollbacks"] += 1

        threshold = self.rollback_ratio_threshold
        for svc, stats in svc_stats.items():
            total = stats["deploys"] + stats["rollbacks"]
            if total == 0:
                continue
            ratio = stats["rollbacks"] / total
            if ratio > threshold:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Service '{svc}' has a high rollback ratio: "
                            f"{stats['rollbacks']}/{total} ({ratio:.0%}) "
                            f"exceeds threshold of {threshold:.0%}. "
                            "Maintainability may be declining."
                        ),
                    )
                )

    # ------------------------------------------------------------------
    # Rule 3 – co-deployment coupling
    # ------------------------------------------------------------------
    def _check_co_deployment(self) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        if len(events) < 2:
            return

        # Group deploy timestamps per service (only successful deploys)
        svc_times: dict[str, list[datetime.datetime]] = defaultdict(list)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("action") != "deploy":
                continue
            ts_str = ev.get("deployed_at", "")
            if not ts_str:
                continue
            try:
                svc_times[ev.get("service", "")].append(
                    datetime.datetime.fromisoformat(ts_str)
                )
            except ValueError:
                continue

        svc_names = list(svc_times.keys())
        overlap_threshold = self.co_deploy_overlap_threshold

        for i in range(len(svc_names)):
            for j in range(i + 1, len(svc_names)):
                a = svc_names[i]
                b = svc_names[j]
                overlap = self._time_overlap(svc_times[a], svc_times[b])
                if overlap > overlap_threshold:
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.INFO,
                            message=(
                                f"Services '{a}' and '{b}' are deployed together "
                                f"{overlap:.0%} of the time. "
                                "They may be too tightly coupled – consider consolidating."
                            ),
                        )
                    )

    @staticmethod
    def _time_overlap(
        times_a: list[datetime.datetime],
        times_b: list[datetime.datetime],
        window_minutes: int = 5,
    ) -> float:
        """Return the fraction of *a* deployments that have a *b* deployment
        within *window_minutes* (bidirectional)."""
        if not times_a or not times_b:
            return 0.0
        window = datetime.timedelta(minutes=window_minutes)

        overlap_a = 0
        for ta in times_a:
            if any(abs(ta - tb) <= window for tb in times_b):
                overlap_a += 1

        overlap_b = 0
        for tb in times_b:
            if any(abs(tb - ta) <= window for ta in times_a):
                overlap_b += 1

        ratio_a = overlap_a / len(times_a)
        ratio_b = overlap_b / len(times_b)
        return max(ratio_a, ratio_b)

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
