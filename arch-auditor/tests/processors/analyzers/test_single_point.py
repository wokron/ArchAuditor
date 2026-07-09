from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_single_point_analyzer_identifies_critical_node():
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
            "SinglePointAnalyzer": {
                "warning_percentage_threshold": 10.0,
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.system_state.graph.nodes["ServiceA"]["call_count"] = 100
    auditor.system_state.graph.nodes["ServiceB"]["call_count"] = 90
    auditor.system_state.graph.nodes["ServiceC"]["call_count"] = 5
    auditor.system_state.graph.nodes["ServiceD"]["call_count"] = 1

    auditor.invoke()
    summary = auditor.system_state.extra_attrs["single_point_summary"]

    assert summary["root"] == "ServiceA"
    assert any(node["service"] == "ServiceB" for node in summary["critical_nodes"])
    assert any("critical single point of failure" in msg for msg in reporter.messages)
