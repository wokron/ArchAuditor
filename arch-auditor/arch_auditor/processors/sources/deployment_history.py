from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class DeploymentHistorySource(Processor):
    """Source for deployment/rollback event history.

    Each event is a dict with:
        service       - service name
        action        - "deploy" | "rollback"
        version       - version string (e.g. "v1.2.3")
        deployed_at   - ISO-format timestamp
        success       - True if deployment succeeded without rollback
    """

    @staticmethod
    def name() -> str:
        return "DeploymentHistorySource"

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
            self.context.system_state.extra_attrs["deployment_history"] = self.events

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
