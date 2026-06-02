import datetime
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class ConfigDriftAnalyzer(Processor):
    """Detect manual configuration changes that have persisted too long.

    A "manual" change is one where changed_by is not a recognised controller.
    If it stays unreverted beyond *drift_threshold_hours* a WARNING is raised.
    """

    @staticmethod
    def name() -> str:
        return "ConfigDriftAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ConfigDriftSource"]

    def init(self, config) -> bool:
        self.drift_threshold_hours = config.get("drift_threshold_hours", 24)
        return True

    def process(self) -> None:
        events = self.context.system_state.extra_attrs.get("config_drift_events", [])
        if not events:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        controller_kinds = {"controller", "operator", "system", "gitops"}

        for ev in events:
            if not isinstance(ev, dict):
                continue
            changed_by = (ev.get("changed_by") or "").lower()
            if changed_by in controller_kinds:
                continue  # automated change – ignore

            changed_at_str = ev.get("changed_at")
            if not changed_at_str:
                continue

            try:
                changed_at = datetime.datetime.fromisoformat(changed_at_str)
            except ValueError:
                continue

            delta = now - changed_at
            if delta > datetime.timedelta(hours=self.drift_threshold_hours):
                resource = ev.get("resource", "unknown")
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Configuration drift detected for '{resource}': "
                            f"manual change by '{ev.get('changed_by')}' has persisted "
                            f"for {delta.days}d {delta.seconds // 3600}h "
                            f"(threshold: {self.drift_threshold_hours}h)."
                        ),
                    )
                )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
