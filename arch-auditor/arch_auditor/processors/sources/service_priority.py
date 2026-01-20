from ...processors import Processor
from arch_auditor.priority_manager import PriorityManager


class ServicePrioritySource(Processor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.priority_manager: PriorityManager = None

    @staticmethod
    def name() -> str:
        return "ServicePrioritySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config) -> bool:
        if not isinstance(config, PriorityManager):
            return False
        self.priority_manager = config
        return True

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
