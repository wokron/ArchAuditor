from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class ConfigDriftSource(Processor):
    """Source for configuration-change audit events.

    Each event is a dict with:
        resource   - identifier (e.g. "deployment/default/my-svc")
        changed_at - ISO-format timestamp
        changed_by - "controller" | "user" | "unknown"
    """

    @staticmethod
    def name() -> str:
        return "ConfigDriftSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type

        if source_type == "Mock":
            self.events: list[dict] = config.get("events", [])
            return True
        return False

    def process(self) -> None:
        if self.source_type == "Mock":
            self.context.system_state.extra_attrs["config_drift_events"] = self.events
        # Future: read from K8s audit-log API or GitOps webhook

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
