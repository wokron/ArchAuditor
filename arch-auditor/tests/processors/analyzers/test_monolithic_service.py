from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_monolithic_by_degree():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("hub-svc", "A"),
                    ("hub-svc", "B"),
                    ("hub-svc", "C"),
                    ("hub-svc", "D"),
                    ("hub-svc", "E"),
                    ("hub-svc", "F"),
                    ("hub-svc", "G"),
                    ("hub-svc", "H"),
                    ("X", "hub-svc"),
                    ("Y", "hub-svc"),
                    ("Z", "hub-svc"),
                    ("W", "hub-svc"),
                    ("V", "hub-svc"),
                    ("U", "hub-svc"),
                    ("T", "hub-svc"),
                    ("S", "hub-svc"),
                    ("A", "B"),
                ],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "MonolithicServiceAnalyzer": {
                "degree_threshold": 15,
                "resource_multiplier": 5.0,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any("hub-svc" in msg and "high degree" in msg for msg in reporter.messages)


def test_monolithic_by_actual_cpu_usage():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "normal-1",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m"},
                                        "limits": {"cpu": "200m"},
                                    },
                                }
                            ],
                        },
                        {
                            "name": "normal-2",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m"},
                                        "limits": {"cpu": "200m"},
                                    },
                                }
                            ],
                        },
                        {
                            "name": "normal-3",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m"},
                                        "limits": {"cpu": "200m"},
                                    },
                                }
                            ],
                        },
                        {
                            "name": "fat-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "fat:latest",
                                    "resources": {
                                        "requests": {"cpu": "200m"},
                                        "limits": {"cpu": "2"},
                                    },
                                }
                            ],
                        },
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "cpu_usage": {
                        "normal-1": [(0, 0.10), (60, 0.10), (120, 0.10)],
                        "normal-2": [(0, 0.10), (60, 0.10), (120, 0.10)],
                        "normal-3": [(0, 0.10), (60, 0.10), (120, 0.10)],
                        "fat-svc": [(0, 0.80), (60, 0.80), (120, 0.80)],
                    },
                },
            },
            "MonolithicServiceAnalyzer": {
                "degree_threshold": 15,
                "resource_multiplier": 5.0,
                "min_timeseries_points": 3,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "fat-svc" in msg and "CPU utilization" in msg for msg in reporter.messages
    )
    assert not any(
        "normal-" in msg and "CPU utilization" in msg for msg in reporter.messages
    )


def test_monolithic_by_actual_memory_usage():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "checkout")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "normal-1",
                            "namespace": "default",
                            "containers": [{"name": "app", "image": "img"}],
                        },
                        {
                            "name": "normal-2",
                            "namespace": "default",
                            "containers": [{"name": "app", "image": "img"}],
                        },
                        {
                            "name": "normal-3",
                            "namespace": "default",
                            "containers": [{"name": "app", "image": "img"}],
                        },
                        {
                            "name": "fat-mem-svc",
                            "namespace": "default",
                            "containers": [{"name": "app", "image": "img"}],
                        },
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "memory_usage": {
                        "normal-1": [(0, 100.0), (60, 100.0), (120, 100.0)],
                        "normal-2": [(0, 100.0), (60, 100.0), (120, 100.0)],
                        "normal-3": [(0, 100.0), (60, 100.0), (120, 100.0)],
                        "fat-mem-svc": [(0, 1000.0), (60, 1000.0), (120, 1000.0)],
                    },
                },
            },
            "MonolithicServiceAnalyzer": {
                "degree_threshold": 15,
                "resource_multiplier": 5.0,
                "min_timeseries_points": 3,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "fat-mem-svc" in msg and "memory usage" in msg for msg in reporter.messages
    )


def test_monolithic_summary_written():
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
                                    "name": "app-a",
                                    "image": "img",
                                    "resources": {
                                        "requests": {
                                            "cpu": "100m",
                                            "memory": "128Mi",
                                        },
                                        "limits": {
                                            "cpu": "300m",
                                            "memory": "256Mi",
                                        },
                                    },
                                },
                                {
                                    "name": "app-b",
                                    "image": "img",
                                    "resources": {
                                        "requests": {
                                            "cpu": "200m",
                                            "memory": "256Mi",
                                        },
                                        "limits": {
                                            "cpu": "400m",
                                            "memory": "512Mi",
                                        },
                                    },
                                },
                            ],
                        }
                    ],
                    "pods": [],
                },
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "cpu_usage": {
                        "app-a": [(0, 0.10), (60, 0.10), (120, 0.10)],
                        "app-b": [(0, 0.20), (60, 0.20), (120, 0.20)],
                    },
                    "memory_usage": {
                        "app-a": [(0, 100.0), (60, 100.0), (120, 100.0)],
                        "app-b": [(0, 200.0), (60, 200.0), (120, 200.0)],
                    },
                },
            },
            "MonolithicServiceAnalyzer": {
                "degree_threshold": 10,
                "resource_multiplier": 5.0,
                "min_timeseries_points": 3,
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["monolithic_service_summary"]
    checkout = next(item for item in summary if item["service"] == "checkout")
    assert abs(checkout["avg_cpu_usage"] - 0.3) < 1e-9
    assert abs(checkout["avg_memory_usage_bytes"] - 300.0) < 1e-9
    assert checkout["cpu_timeseries_points"] == 3
    assert checkout["memory_timeseries_points"] == 3
    assert abs(checkout["total_request_cpu"] - 0.3) < 1e-9
    assert abs(checkout["total_limit_cpu"] - 0.7) < 1e-9
    assert checkout["total_request_memory_bytes"] == 384 * 1024 * 1024
    assert checkout["total_limit_memory_bytes"] == 768 * 1024 * 1024
    assert checkout["degree"] == 2
    assert checkout["in_degree"] == 1
    assert checkout["out_degree"] == 1
