from arch_auditor.processors.processor import Processor, ProcessorRegistry
from arch_auditor.system_state import SystemState
from arch_auditor.scheduler import Scheduler
from arch_auditor.context import AuditContext


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
        self.context.system_state.extra_attrs["order"].append("MockProcessor1")

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
        self.context.system_state.extra_attrs["order"].append("MockProcessor2")

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
        self.context.system_state.extra_attrs["order"].append("MockProcessor3")

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


registry = ProcessorRegistry()
registry.register(MockProcessor1)
registry.register(MockProcessor2)
registry.register(MockProcessor3)

system_state = SystemState()
context = AuditContext(system_state)


def test_scheduler_order():
    system_state.extra_attrs["order"] = []
    processors = [
        MockProcessor3(context),
        MockProcessor1(context),
        MockProcessor2(context),
    ]
    scheduler = Scheduler(processors)
    scheduler.process()
    assert system_state.extra_attrs["order"] == [
        "MockProcessor2",
        "MockProcessor1",
        "MockProcessor3",
    ]
