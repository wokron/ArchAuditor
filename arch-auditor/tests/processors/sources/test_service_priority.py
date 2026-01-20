from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_service_priority_source():
    priority_manager = InMemoryPriorityManager()

    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                ],
            },
            "ServicePrioritySource": priority_manager,
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    priority_manager.set_priority("ServiceA", 1)
    priority_manager.set_priority("ServiceB", 2)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 0  # No issues should be reported

    assert auditor.system_state.graph.nodes["ServiceA"]["priority"] == 1
    assert auditor.system_state.graph.nodes["ServiceB"]["priority"] == 2
    # Default priority for unspecified services
    assert auditor.system_state.graph.nodes["ServiceC"]["priority"] == 0
