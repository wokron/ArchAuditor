import datetime
from collections import defaultdict

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class MaintainabilityAnalyzer(Processor):
    """Assess whether services are becoming harder to maintain.

    Rules:
      - Pod start-up time (creation -> ready) > threshold -> WARNING
      - Deployment frequency much lower than the cluster median -> INFO
      - High rollback ratio -> WARNING
      - Two services always deployed together -> INFO
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
        self.low_deploy_frequency_ratio = config.get(
            "low_deploy_frequency_ratio", 0.5
        )
        self.co_deploy_overlap_threshold = config.get(
            "co_deploy_overlap_threshold", 0.8
        )
        self.min_co_deploy_events_per_service = config.get(
            "min_co_deploy_events_per_service", 3
        )
        return True

    def process(self) -> None:
        self.summary = {
            "startup_issues": [],
            "deploy_frequency_issues": [],
            "rollback_issues": [],
            "co_deployment_issues": [],
            "history_observability": {},
            "startup_threshold_seconds": self.startup_threshold_seconds,
            "rollback_ratio_threshold": self.rollback_ratio_threshold,
            "low_deploy_frequency_ratio": self.low_deploy_frequency_ratio,
            "co_deploy_overlap_threshold": self.co_deploy_overlap_threshold,
            "min_co_deploy_events_per_service": self.min_co_deploy_events_per_service,
            "deployment_history_source": self.context.system_state.extra_attrs.get(
                "deployment_history_source"
            ),
        }
        self._summarize_history()
        self._check_startup_time()
        self._check_deploy_frequency()
        self._check_rollback_rate()
        self._check_co_deployment()
        self.context.system_state.extra_attrs["maintainability_summary"] = self.summary

    def _summarize_history(self) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        deploy_counts: dict[str, int] = defaultdict(int)
        rollback_counts: dict[str, int] = defaultdict(int)
        action_counts: dict[str, int] = defaultdict(int)

        for ev in events:
            if not isinstance(ev, dict):
                continue
            svc = ev.get("service", "") or "unknown"
            action = (ev.get("action") or "unknown").lower()
            action_counts[action] += 1
            if action == "deploy":
                deploy_counts[svc] += 1
            elif action == "rollback":
                rollback_counts[svc] += 1

        services_with_enough_co_deploy_events = sorted(
            svc
            for svc, count in deploy_counts.items()
            if count >= self.min_co_deploy_events_per_service
        )

        notes = []
        if not events:
            notes.append("no deployment history events were collected")
        if action_counts.get("rollback", 0) == 0:
            notes.append("no rollback events were observed")
        if len(services_with_enough_co_deploy_events) < 2:
            notes.append(
                "not enough services have the minimum deploy event count required "
                "for co-deployment analysis"
            )

        self.summary["history_observability"] = {
            "event_count": len(events),
            "action_counts": dict(sorted(action_counts.items())),
            "deploy_counts_by_service": dict(sorted(deploy_counts.items())),
            "rollback_counts_by_service": dict(sorted(rollback_counts.items())),
            "services_with_enough_co_deploy_events": services_with_enough_co_deploy_events,
            "notes": notes,
        }

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
                svc = self._resolve_service_name(pod)
                issue = {
                    "service": svc,
                    "pod": pod.get("name", "unknown"),
                    "namespace": pod.get("namespace"),
                    "startup_seconds": round(duration, 2),
                    "threshold_seconds": threshold,
                }
                self.summary["startup_issues"].append(issue)
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

    def _check_deploy_frequency(self) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        deploy_events_by_service: dict[str, list[dict]] = defaultdict(list)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if (ev.get("action") or "").lower() != "deploy":
                continue
            svc = ev.get("service", "")
            if not svc:
                continue
            deploy_events_by_service[svc].append(ev)

        if len(deploy_events_by_service) < 2:
            return

        deploy_counts = sorted(len(items) for items in deploy_events_by_service.values())
        median_count = self._median(deploy_counts)
        if median_count <= 0:
            return

        threshold_count = median_count * self.low_deploy_frequency_ratio
        for svc, svc_events in sorted(deploy_events_by_service.items()):
            deploy_count = len(svc_events)
            if deploy_count >= threshold_count:
                continue

            deployed_times = sorted(
                ev.get("deployed_at", "")
                for ev in svc_events
                if ev.get("deployed_at")
            )
            issue = {
                "service": svc,
                "deploy_count": deploy_count,
                "cluster_median_deploy_count": median_count,
                "low_deploy_frequency_ratio": self.low_deploy_frequency_ratio,
                "threshold_count": round(threshold_count, 2),
                "first_deployed_at": deployed_times[0] if deployed_times else None,
                "latest_deployed_at": deployed_times[-1] if deployed_times else None,
            }
            self.summary["deploy_frequency_issues"].append(issue)
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.INFO,
                    message=(
                        f"Service '{svc}' has a relatively low deployment frequency: "
                        f"{deploy_count} deploy events observed, below "
                        f"{self.low_deploy_frequency_ratio:.0%} of the cluster median "
                        f"({median_count}). This may indicate reduced maintainability "
                        "or a more difficult release process."
                    ),
                )
            )

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
                issue = {
                    "service": svc,
                    "deploy_count": stats["deploys"],
                    "rollback_count": stats["rollbacks"],
                    "total_events": total,
                    "rollback_ratio": round(ratio, 4),
                    "threshold": threshold,
                }
                self.summary["rollback_issues"].append(issue)
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

    def _check_co_deployment(self) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        if len(events) < 2:
            return

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
        min_events = self.min_co_deploy_events_per_service

        for i in range(len(svc_names)):
            for j in range(i + 1, len(svc_names)):
                a = svc_names[i]
                b = svc_names[j]
                count_a = len(svc_times[a])
                count_b = len(svc_times[b])
                if count_a < min_events or count_b < min_events:
                    continue
                overlap = self._time_overlap(svc_times[a], svc_times[b])
                if overlap > overlap_threshold:
                    issue = {
                        "service_a": a,
                        "service_b": b,
                        "deploy_events_a": count_a,
                        "deploy_events_b": count_b,
                        "overlap_ratio": round(overlap, 4),
                        "threshold": overlap_threshold,
                        "min_events_per_service": min_events,
                    }
                    self.summary["co_deployment_issues"].append(issue)
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.INFO,
                            message=(
                                f"Services '{a}' and '{b}' are deployed together "
                                f"{overlap:.0%} of the time "
                                f"({count_a} vs {count_b} deploy events observed). "
                                "They may be too tightly coupled - consider consolidating."
                            ),
                        )
                    )

    @staticmethod
    def _resolve_service_name(pod: dict) -> str:
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

        service_name = pod.get("service_name")
        if service_name:
            return service_name

        pod_name = pod.get("name") or "unknown"
        if "-" in pod_name:
            parts = pod_name.split("-")
            if len(parts) >= 3:
                return "-".join(parts[:-2])
            return "-".join(parts[:-1]) or pod_name
        return pod_name

    @staticmethod
    def _time_overlap(
        times_a: list[datetime.datetime],
        times_b: list[datetime.datetime],
        window_minutes: int = 5,
    ) -> float:
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
    def _median(values: list[int]) -> float:
        if not values:
            return 0.0
        mid = len(values) // 2
        if len(values) % 2 == 1:
            return float(values[mid])
        return (values[mid - 1] + values[mid]) / 2.0

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
