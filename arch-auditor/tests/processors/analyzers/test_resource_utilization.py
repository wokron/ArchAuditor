from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_sync_path_service_without_request_is_flagged():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout"), ("checkout", "payment")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "checkout",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "checkout",
                                    "image": "img",
                                    "resources": {"requests": {}, "limits": {}},
                                }
                            ],
                        }
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "throughput": {
                        "frontend": [(0, 10.0), (60, 12.0), (120, 11.0)],
                        "checkout": [(0, 9.0), (60, 10.0), (120, 11.0)],
                    }
                },
            },
            "ResourceUtilizationAnalyzer": {
                "sync_entry_services": ["frontend"],
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "user-facing synchronous call path" in msg and "checkout" in msg
        for msg in reporter.messages
    )


def test_high_qps_async_service_without_request_is_flagged():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout"), ("worker", "sink")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "worker",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "worker",
                                    "image": "img",
                                    "resources": {"requests": {}, "limits": {}},
                                }
                            ],
                        }
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "throughput": {
                        "frontend": [(0, 5.0), (60, 5.0), (120, 5.0)],
                        "checkout": [(0, 4.0), (60, 4.0), (120, 4.0)],
                        "worker": [(0, 40.0), (60, 45.0), (120, 50.0)],
                        "sink": [(0, 2.0), (60, 2.0), (120, 2.0)],
                    }
                },
            },
            "ResourceUtilizationAnalyzer": {
                "sync_entry_services": ["frontend"],
                "high_qps_multiplier": 3.0,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "very high throughput" in msg and "worker" in msg for msg in reporter.messages
    )


def test_resource_utilization_summary_contains_sync_async_flags():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout"), ("worker", "sink")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "checkout",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "checkout",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "128Mi"},
                                        "limits": {"cpu": "500m", "memory": "256Mi"},
                                    },
                                }
                            ],
                        }
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "throughput": {
                        "frontend": [(0, 10.0), (60, 12.0), (120, 11.0)],
                        "checkout": [(0, 9.0), (60, 9.5), (120, 10.0)],
                    },
                    "cpu_usage": {"checkout": [(0, 0.04), (60, 0.05), (120, 0.06)]},
                    "memory_usage": {
                        "checkout": [
                            (0, 80_000_000.0),
                            (60, 82_000_000.0),
                            (120, 84_000_000.0),
                        ]
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "sync_entry_services": ["frontend"],
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["resource_utilization_summary"]
    assert len(summary) == 1
    assert summary[0]["is_sync_path_service"] is True
    assert summary[0]["is_async_only_service"] is False
    assert summary[0]["requires_request"] is True
    assert summary[0]["avg_throughput"] is not None
