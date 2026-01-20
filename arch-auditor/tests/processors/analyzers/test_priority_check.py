from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_priority_check_analyze_check_strong_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                    ("ServiceC", "ServiceD"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "ServiceA", "to": "ServiceB", "type": "strong"},
                    {"from": "ServiceB", "to": "ServiceC", "type": "strong"},
                    {"from": "ServiceC", "to": "ServiceD", "type": "strong"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceA", 3)
    auditor.priority_manager.set_priority("ServiceB", 1)
    auditor.priority_manager.set_priority("ServiceC", 2)
    auditor.priority_manager.set_priority("ServiceD", 0)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 1  # B -> C is a violation
    assert any(
        "Priority violation: 'ServiceB' (priority: 1) depends strongly on 'ServiceC' (priority: 2)."
        in msg
        for msg in messages
    )


def test_priority_check_analyze_check_weak_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceX", "ServiceY"),
                    ("ServiceZ", "ServiceY"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "ServiceX", "to": "ServiceY", "type": "weak"},
                    {"from": "ServiceZ", "to": "ServiceY", "type": "weak"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceX", 1)
    auditor.priority_manager.set_priority("ServiceY", 2)
    auditor.priority_manager.set_priority("ServiceZ", 3)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 1  # Suggestion for ServiceY
    assert any(
        "Consider reviewing 'ServiceY' (priority: 2) as it is only weakly depended upon by services ['ServiceX', 'ServiceZ']."
        in msg
        for msg in messages
    )
