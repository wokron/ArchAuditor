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

        if type == "InMemory":
            self.priority_manager = InMemoryPriorityManager()
            return True
        else:
            return False

    def process(self) -> None:
        G = self.context.system_state.graph
        for node in G.nodes:
            priority = self.priority_manager.get_priority(node)
            if priority is None:
                priority = 0  # Default priority is the highest
            G.nodes[node]["priority"] = priority

    @staticmethod
    def has_visualization() -> bool:
        return False

    @staticmethod
    def visualize():
        pass
