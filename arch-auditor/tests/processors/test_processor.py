import pytest
from arch_auditor.processors.processor import Processor
from arch_auditor.processors.processor import ProcessorRegistry, ProcessorsBuilder
from arch_auditor.system_state import SystemState


class MockProcessor1(Processor):
    @staticmethod
    def name() -> str:
        return "MockProcessor1"

    @staticmethod
    def requires() -> list[str]:
        return ["MockProcessor2"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class MockProcessor2(Processor):
    @staticmethod
    def name() -> str:
        return "MockProcessor2"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class MockProcessor3(Processor):
    @staticmethod
    def name() -> str:
        return "MockProcessor3"

    @staticmethod
    def requires() -> list[str]:
        return ["MockProcessor1", "MockProcessor2"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class FailingProcessor(Processor):
    @staticmethod
    def name() -> str:
        return "FailingProcessor"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config) -> bool:
        return False

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class MockProcessor4(Processor):
    @staticmethod
    def name() -> str:
        return "MockProcessor4"

    @staticmethod
    def requires() -> list[str]:
        return ["MockProcessor2", "FailingProcessor"]

    def init(self, config) -> bool:
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


registry = ProcessorRegistry()
registry.register(MockProcessor1)
registry.register(MockProcessor2)
registry.register(MockProcessor3)
registry.register(FailingProcessor)
registry.register(MockProcessor4)

system_state = SystemState()


def test_processors_builder_with_dependencies():
    processors_config = {
        "MockProcessor3": {},
    }

    builder = ProcessorsBuilder(processors_config, system_state, registry)
    processors = builder.build_processors()

    processor_names = [processor.name() for processor in processors]
    assert "MockProcessor1" in processor_names
    assert "MockProcessor2" in processor_names
    assert "MockProcessor3" in processor_names


def test_processors_builder_with_failing_dependency():
    processors_config = {
        "MockProcessor4": {},
    }

    builder = ProcessorsBuilder(processors_config, system_state, registry)
    with pytest.raises(Exception):
        processors = builder.build_processors()
