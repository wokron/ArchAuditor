from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_circular_dependency_analyze():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("A", "B"),
                    ("B", "C"),
                    ("C", "A"),
                    ("D", "E"),
                    ("E", "F"),
                    ("F", "D"),
                    ("G", "H"),
                    ("A", "D"),
                ],
            },
            "CircularDependencyAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 4
    assert any("Circular dependency detected" in msg for msg in messages)
    assert any("Suggested resolution" in msg for msg in messages)

    summary = auditor.system_state.extra_attrs["circular_dependency_summary"]
    assert summary["count"] == 2
