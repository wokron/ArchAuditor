from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_single_point_analyzer_priority_reverse():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceA", "ServiceC"),
                    ("ServiceB", "ServiceD"),
                    ("ServiceC", "ServiceD"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "SinglePointAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceA", 3)
    auditor.priority_manager.set_priority("ServiceB", 2)
    auditor.priority_manager.set_priority("ServiceC", 1)
    auditor.priority_manager.set_priority("ServiceD", 0)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 3
