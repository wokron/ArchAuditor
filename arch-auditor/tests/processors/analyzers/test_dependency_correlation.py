from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_dependency_correlation_flags_high_pearson_correlation():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {
                        "from": "frontend",
                        "to": "checkout",
                        "type": "strong",
                        "correlation": 0.82,
                        "call_count": 220,
                    }
                ],
            },
            "DependencyCorrelationAnalyzer": {
                "warning_correlation_threshold": 0.7,
                "error_correlation_threshold": 0.9,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "Strong dependency detected from Pearson correlation" in msg
        and "'frontend' -> 'checkout'" in msg
        and "0.8200" in msg
        for msg in reporter.messages
    )


def test_dependency_correlation_ignores_insufficient_data_edges():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {
                        "from": "frontend",
                        "to": "checkout",
                        "type": "strong",
                        "dependency_correlation": None,
                        "dependency_correlation_status": "insufficient_data",
                    }
                ],
            },
            "DependencyCorrelationAnalyzer": {
                "warning_correlation_threshold": 0.7,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert reporter.messages == []


def test_dependency_correlation_uses_error_level_for_very_high_correlation():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("checkout", "currency")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {
                        "from": "checkout",
                        "to": "currency",
                        "type": "strong",
                        "correlation": 0.96,
                        "call_count": 600,
                    }
                ],
            },
            "DependencyCorrelationAnalyzer": {
                "warning_correlation_threshold": 0.7,
                "error_correlation_threshold": 0.9,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "[ERROR]" in msg and "'checkout' -> 'currency'" in msg
        for msg in reporter.messages
    )
