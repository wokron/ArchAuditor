from ...processors import Processor
import networkx as nx


class ServiceDependencySource(Processor):
    @staticmethod
    def name() -> str:
        return "ServiceDependencySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        type = config.get("type", None)
        if type is None:
            return False

        if type == "Mock":
            dependencies = config.get("dependencies", [])
            edge_attrs = {}
            for dep in dependencies:
                from_service = dep.get("from")
                to_service = dep.get("to")
                type = dep.get("type", "unknown")
                edge_attrs[(from_service, to_service)] = {"dependency_type": type}
            nx.set_edge_attributes(self.context.system_state.graph, edge_attrs)
            return True
        else:
            # Unknown type
            return False

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
