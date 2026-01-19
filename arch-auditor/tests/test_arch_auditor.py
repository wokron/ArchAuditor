from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.processors import Processor, ProcessorRegistry
import networkx as nx


class MockProcessor(Processor):
    @staticmethod
    def name() -> str:
        return "MockProcessor"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config) -> bool:
        assert config["setting1"] is True
        assert config["setting2"] == "value"
        self.context.system_state.extra_attrs["init_called"] = True
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


external_registry = ProcessorRegistry()
external_registry.register(MockProcessor)


def test_arch_auditor_init():
    config = {
        "processors": {
            "MockProcessor": {
                "setting1": True,
                "setting2": "value",
            }
        }
    }
    auditor = ArchAuditor(config, registry=external_registry)
    assert auditor.config == config
    assert auditor.system_state is not None
    assert auditor.registry is not None
    assert auditor.reporter is not None
    assert auditor.processors is not None
    assert auditor.scheduler is not None
    assert auditor.system_state.extra_attrs.get("init_called") is True


def test_arch_auditor_invoke():
    config = {
        "processors": {
            "MockProcessor": {
                "setting1": True,
                "setting2": "value",
            }
        }
    }
    auditor = ArchAuditor(config, registry=external_registry)
    auditor.invoke()
    assert auditor.system_state.extra_attrs.get("init_called") is True


class MockDataSource(Processor):
    @staticmethod
    def name() -> str:
        return "MockDataSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        # A simple dag
        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (1, 3), (2, 4), (3, 4)])
        self.context.system_state.graph = G

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class MockAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "MockAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["MockDataSource"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        self.context.system_state.extra_attrs["node_count"] = G.number_of_nodes()
        self.context.system_state.extra_attrs["edge_count"] = G.number_of_edges()

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


data_registry = ProcessorRegistry()
data_registry.register(MockDataSource)
data_registry.register(MockAnalyzer)


def test_arch_auditor_data_flow():
    config = {
        "processors": {
            "MockAnalyzer": {},
        }
    }
    auditor = ArchAuditor(config, registry=data_registry)
    auditor.invoke()
    assert auditor.system_state.extra_attrs.get("node_count") == 4
    assert auditor.system_state.extra_attrs.get("edge_count") == 4
