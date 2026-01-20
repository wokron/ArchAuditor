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
            "CircularDependencyAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    edges = [
        ("A", "B"),
        ("B", "C"),
        ("C", "A"),  # This creates a cycle A -> B -> C ->
        ("D", "E"),
        ("E", "F"),
        ("F", "D"),  # This creates a cycle D -> E -> F ->
        ("G", "H"),
        ("A", "D"),  # No cycle here
    ]

    auditor.system_state.graph.add_edges_from(edges)
    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 4
    assert any("Circular dependency detected" in msg for msg in messages)
    assert any("Suggested resolution" in msg for msg in messages)
