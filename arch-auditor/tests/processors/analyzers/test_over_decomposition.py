from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_over_decomposition_analyze():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("A", "B"),
                    ("B", "C"),
                    ("C", "D"),
                    ("D", "E"),
                    ("E", "F"),
                    ("F", "G"),  # Long chain A -> B -> C -> D -> E -> F -> G
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 2
    assert any("Long dependency chain detected" in msg for msg in messages)
    assert any("pipe services" in msg for msg in messages)
