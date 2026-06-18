from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_priority_check_flags_core_service_strong_dependency_on_edge_service():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("caller-a", "core-service"),
                    ("caller-b", "core-service"),
                    ("caller-c", "core-service"),
                    ("core-service", "edge-service"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "caller-a", "to": "core-service", "type": "weak"},
                    {"from": "caller-b", "to": "core-service", "type": "weak"},
                    {"from": "caller-c", "to": "core-service", "type": "weak"},
                    {"from": "core-service", "to": "edge-service", "type": "strong"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()

    assert any(
        "Dependency hierarchy violation: core service 'core-service'" in msg
        and "edge service 'edge-service'" in msg
        for msg in reporter.messages
    )


def test_priority_check_does_not_flag_non_strong_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("caller-a", "core-service"),
                    ("caller-b", "core-service"),
                    ("core-service", "edge-service"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "caller-a", "to": "core-service", "type": "weak"},
                    {"from": "caller-b", "to": "core-service", "type": "weak"},
                    {"from": "core-service", "to": "edge-service", "type": "weak"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()

    assert reporter.messages == []


def test_priority_check_does_not_flag_when_target_is_also_core():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("caller-a", "core-a"),
                    ("caller-b", "core-a"),
                    ("caller-c", "core-b"),
                    ("caller-d", "core-b"),
                    ("core-a", "core-b"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "caller-a", "to": "core-a", "type": "weak"},
                    {"from": "caller-b", "to": "core-a", "type": "weak"},
                    {"from": "caller-c", "to": "core-b", "type": "weak"},
                    {"from": "caller-d", "to": "core-b", "type": "weak"},
                    {"from": "core-a", "to": "core-b", "type": "strong"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()

    assert reporter.messages == []


def test_priority_check_flags_when_indegree_is_equal_but_centrality_gap_is_large():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("caller-a", "core-service"),
                    ("core-service", "edge-service"),
                    ("core-service", "support-a"),
                    ("core-service", "support-b"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "caller-a", "to": "core-service", "type": "weak"},
                    {"from": "core-service", "to": "edge-service", "type": "strong"},
                    {"from": "core-service", "to": "support-a", "type": "weak"},
                    {"from": "core-service", "to": "support-b", "type": "weak"},
                ],
            },
            "PriorityCheckAnalyzer": {
                "min_core_indegree": 1,
                "high_pagerank_percentile": 0.7,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.invoke()

    assert any(
        "Dependency hierarchy violation: core service 'core-service'" in msg
        and "edge service 'edge-service'" in msg
        for msg in reporter.messages
    )
