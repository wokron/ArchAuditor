from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_over_decomposition_long_chain_and_pipe_services():
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
                    ("F", "G"),
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "path_service_threshold": 5,
                "pipe_service_ratio_threshold": 0.3,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    messages = reporter.messages
    assert any("Long dependency chains detected" in msg for msg in messages)
    assert any("pipe services" in msg for msg in messages)

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["has_long_chain_issue"] is True
    assert summary["longest_path_service_count"] == 7
    assert summary["long_path_count"] == 1
    assert summary["top_long_paths"][0]["services"] == [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
    ]
    assert summary["has_pipe_service_ratio_issue"] is True
    assert [item["service"] for item in summary["pipe_services"]] == [
        "B",
        "C",
        "D",
        "E",
        "F",
    ]


def test_over_decomposition_co_deployed_pairs():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("frontend", "adapter"),
                    ("adapter", "payment"),
                ],
            },
            "SingleRootDAGSource": {},
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {
                        "service": "frontend",
                        "action": "deploy",
                        "deployed_at": "2026-06-08T10:00:00+00:00",
                    },
                    {
                        "service": "adapter",
                        "action": "deploy",
                        "deployed_at": "2026-06-08T10:03:00+00:00",
                    },
                    {
                        "service": "frontend",
                        "action": "deploy",
                        "deployed_at": "2026-06-08T11:00:00+00:00",
                    },
                    {
                        "service": "adapter",
                        "action": "deploy",
                        "deployed_at": "2026-06-08T11:04:00+00:00",
                    },
                ],
            },
            "OverDecompositionAnalyzer": {
                "co_deploy_overlap_threshold": 0.8,
                "co_deploy_window_minutes": 5,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    messages = reporter.messages
    assert any("deployed together" in msg for msg in messages)

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert len(summary["co_deployed_pairs"]) == 1
    pair = summary["co_deployed_pairs"][0]
    assert {pair["service_a"], pair["service_b"]} == {"adapter", "frontend"}
    assert pair["overlap_ratio"] == 1.0


def test_over_decomposition_path_latency_summary():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("frontend", "checkout"),
                    ("checkout", "payment"),
                    ("payment", "email"),
                    ("email", "shipping"),
                    ("shipping", "quote"),
                    ("quote", "carrier"),
                ],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "latency": {
                        "frontend": [(0, 10.0), (60, 10.0), (120, 10.0)],
                        "checkout": [(0, 20.0), (60, 20.0), (120, 20.0)],
                        "payment": [(0, 30.0), (60, 30.0), (120, 30.0)],
                    }
                },
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "path_service_threshold": 5,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["has_long_chain_issue"] is True
    assert summary["longest_path_total_avg_latency"] == 60.0


def test_over_decomposition_flags_path_at_threshold():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("frontend", "checkout"),
                    ("checkout", "payment"),
                    ("payment", "email"),
                    ("email", "shipping"),
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "path_service_threshold": 5,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["longest_path_service_count"] == 5
    assert summary["has_long_chain_issue"] is True


def test_over_decomposition_long_chain_with_cycle():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("frontend-web", "frontend-proxy"),
                    ("frontend-proxy", "frontend"),
                    ("frontend", "checkout"),
                    ("checkout", "shipping"),
                    ("shipping", "quote"),
                    ("frontend", "recommendation"),
                    ("recommendation", "frontend"),
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "path_service_threshold": 5,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["longest_path_services"] == [
        "frontend-web",
        "frontend-proxy",
        "frontend",
        "checkout",
        "shipping",
        "quote",
    ]
    assert summary["longest_path_service_count"] == 6
    assert summary["has_long_chain_issue"] is True


def test_over_decomposition_reports_top_long_paths():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("entry", "a1"),
                    ("a1", "a2"),
                    ("a2", "a3"),
                    ("a3", "a4"),
                    ("entry", "b1"),
                    ("b1", "b2"),
                    ("b2", "b3"),
                    ("b3", "b4"),
                    ("b4", "b5"),
                    ("entry", "c1"),
                    ("c1", "c2"),
                    ("c2", "c3"),
                    ("c3", "c4"),
                    ("c4", "c5"),
                    ("c5", "c6"),
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "path_service_threshold": 5,
                "long_path_limit": 2,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["has_long_chain_issue"] is True
    assert summary["long_path_count"] == 3
    assert summary["long_path_limit"] == 2
    assert len(summary["top_long_paths"]) == 2
    assert summary["top_long_paths"][0]["services"] == [
        "entry",
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6",
    ]
    assert summary["top_long_paths"][1]["services"] == [
        "entry",
        "b1",
        "b2",
        "b3",
        "b4",
        "b5",
    ]


def test_over_decomposition_detects_fanout_amplification():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("frontend-proxy", "frontend"),
                    ("frontend", "ad"),
                    ("frontend", "product-catalog"),
                ],
            },
            "SingleRootDAGSource": {},
            "OverDecompositionAnalyzer": {
                "fanout_amplification_threshold": 2.0,
                "min_upstream_calls": 5,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    graph = auditor.system_state.graph
    graph.edges["frontend-proxy", "frontend"]["call_count"] = 10
    graph.edges["frontend", "ad"]["call_count"] = 42
    graph.edges["frontend", "product-catalog"]["call_count"] = 7

    auditor.invoke()

    summary = auditor.system_state.extra_attrs["over_decomposition_summary"]
    assert summary["has_fanout_amplification_issue"] is True
    assert summary["fanout_amplification_services"][0]["service"] == "frontend"
    assert any("Fan-out amplification detected" in msg for msg in reporter.messages)
