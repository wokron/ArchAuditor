from ...processors import Processor
from arch_auditor.priority_manager import InMemoryPriorityManager, PriorityManager


class ServicePrioritySource(Processor):
    priority_manager: PriorityManager = None

    @staticmethod
    def name() -> str:
        return "ServicePrioritySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        config = config or {}
        self.default_priority = config.get("default_priority")
        if self.priority_manager is None:
            self.priority_manager = InMemoryPriorityManager()
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        explicit_priorities = dict(self.priority_manager.list_priorities())
        self.context.system_state.extra_attrs["service_priorities"] = explicit_priorities
        self.context.system_state.extra_attrs["service_priority_default"] = (
            self.default_priority
        )
        for node in graph.nodes:
            priority = self.priority_manager.get_priority(node)
            if priority is None:
                priority = self.default_priority
            if priority is not None:
                graph.nodes[node]["priority"] = priority

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
