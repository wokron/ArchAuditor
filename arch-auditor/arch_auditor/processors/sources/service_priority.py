from ...processors import Processor
from arch_auditor.priority_manager import PriorityManager, InMemoryPriorityManager


class ServicePrioritySource(Processor):
    priority_manager: PriorityManager = None

    @staticmethod
    def name() -> str:
        return "ServicePrioritySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        type = config.get("type", None)
        if type is None:
            return False

        self.default_priority = config.get("default_priority")
        if type == "InMemory":
            self.priority_manager = InMemoryPriorityManager()
            return True
        else:
            return False

    def process(self) -> None:
        G = self.context.system_state.graph
        explicit_priorities = dict(self.priority_manager.list_priorities())
        self.context.system_state.extra_attrs["service_priorities"] = explicit_priorities
        self.context.system_state.extra_attrs["service_priority_default"] = (
            self.default_priority
        )
        for node in G.nodes:
            priority = self.priority_manager.get_priority(node)
            if priority is None:
                priority = self.default_priority
            if priority is not None:
                G.nodes[node]["priority"] = priority

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
