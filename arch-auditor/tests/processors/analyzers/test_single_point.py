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
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "latency": {
                        "ServiceA": [(0, 100), (60, 120)],
                        "ServiceB": [(0, 200), (60, 220)],
                        "ServiceC": [(0, 300), (60, 320)],
                        "ServiceD": [(0, 400), (60, 420)],
                    },
                    "error_rate": {
                        "ServiceA": [(0, 0.01), (60, 0.02)],
                        "ServiceB": [(0, 0.03), (60, 0.04)],
                        "ServiceC": [(0, 0.05), (60, 0.06)],
                        "ServiceD": [(0, 0.07), (60, 0.08)],
                    },
                    "throughput": {
                        "ServiceA": [(0, 1000), (60, 1100)],
                        "ServiceB": [(0, 900), (60, 950)],
                        "ServiceC": [(0, 800), (60, 850)],
                        "ServiceD": [(0, 700), (60, 750)],
                    },
                },
            },
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
