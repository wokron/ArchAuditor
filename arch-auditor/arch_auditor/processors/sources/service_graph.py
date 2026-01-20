from ...processors import Processor
from abc import ABC, abstractmethod


class ServiceGraphSource(Processor):
    @staticmethod
    def name() -> str:
        return "ServiceGraphSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        type = config.get("type", None)
        if type is None:
            return False

        if type == "Mock":
            edges = config.get("edges", [])
            self.context.system_state.graph.add_edges_from(edges)
            return True
        else:
            # Unknown type
            return False

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    @staticmethod
    def visualize():
        pass
