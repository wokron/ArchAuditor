from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.processors.processor import Processor, ProcessorRegistry


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
