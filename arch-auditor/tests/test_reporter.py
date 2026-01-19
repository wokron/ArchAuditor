from arch_auditor.processors.processor import Processor, ProcessorRegistry
from arch_auditor.system_state import SystemState
from arch_auditor.scheduler import Scheduler
from arch_auditor.context import AuditContext
from arch_auditor.reporter import ReportMessage, ReportType, Reporter


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
        self.context.reporter.report(
            ReportMessage(self.name(), ReportType.INFO, "MockProcessor1 processed")
        )

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
        self.context.reporter.report(
            ReportMessage(self.name(), ReportType.INFO, "MockProcessor2 processed")
        )

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
        self.context.reporter.report(
            ReportMessage(self.name(), ReportType.INFO, "MockProcessor3 processed")
        )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


registry = ProcessorRegistry()
registry.register(MockProcessor1)
registry.register(MockProcessor2)
registry.register(MockProcessor3)

system_state = SystemState()
mock_reporter = MockReporter()
context = AuditContext(system_state, mock_reporter)


def test_scheduler_order():
    processors = [
        MockProcessor3(context),
        MockProcessor1(context),
        MockProcessor2(context),
    ]
    scheduler = Scheduler(processors)
    scheduler.process()
    expected_messages = [
        "[info] from MockProcessor2: MockProcessor2 processed",
        "[info] from MockProcessor1: MockProcessor1 processed",
        "[info] from MockProcessor3: MockProcessor3 processed",
    ]
    assert mock_reporter.messages == expected_messages
