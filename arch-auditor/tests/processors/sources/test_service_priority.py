from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_service_priority_source():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    priority_manager = None
    for processor in auditor.processors:
        if processor.name() == "ServicePrioritySource":
            priority_manager = processor.priority_manager
            break
    assert priority_manager is not None

    priority_manager.set_priority("ServiceA", 1)
    priority_manager.set_priority("ServiceB", 2)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 0  # No issues should be reported

    assert auditor.system_state.graph.nodes["ServiceA"]["priority"] == 1
    assert auditor.system_state.graph.nodes["ServiceB"]["priority"] == 2
    # Default priority for unspecified services
    assert auditor.system_state.graph.nodes["ServiceC"]["priority"] == 0


def test_service_priority_source_custom_default_priority():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
                "default_priority": 3,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert auditor.system_state.graph.nodes["ServiceA"]["priority"] == 3
    assert auditor.system_state.graph.nodes["ServiceB"]["priority"] == 3
    assert auditor.system_state.graph.nodes["ServiceC"]["priority"] == 3
