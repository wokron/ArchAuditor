from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_wasting_cpu():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "waste-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "1"},
                                        "limits": {"cpu": "2"},
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
                    "cpu_usage": {
                        "waste-svc": [
                            (0, 0.1),
                            (60, 0.15),
                            (120, 0.12),
                            (180, 0.1),
                            (240, 0.11),
                        ],
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("wasting CPU" in msg for msg in reporter.messages)


def test_wasting_memory():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "mem-waste-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "1", "memory": "1Gi"},
                                        "limits": {"cpu": "2", "memory": "2Gi"},
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
                    "memory_usage": {
                        "mem-waste-svc": [
                            (0, 100_000_000.0),
                            (60, 120_000_000.0),
                            (120, 110_000_000.0),
                        ],
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("wasting memory" in msg for msg in reporter.messages)


def test_limit_risk():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "hot-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "1"},
                                        "limits": {"cpu": "2"},
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
                    "cpu_usage": {
                        "hot-svc": [
                            (0, 1.5),
                            (60, 1.5),
                            (120, 1.5),
                            (180, 1.5),
                            (240, 1.5),
                        ],
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("CPU stability risk" in msg for msg in reporter.messages)


def test_memory_limit_risk():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "mem-hot-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "1", "memory": "1Gi"},
                                        "limits": {"cpu": "2", "memory": "1Gi"},
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
                    "memory_usage": {
                        "mem-hot-svc": [
                            (0, 900_000_000.0),
                            (60, 900_000_000.0),
                            (120, 900_000_000.0),
                            (180, 900_000_000.0),
                            (240, 900_000_000.0),
                        ],
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("memory stability risk" in msg for msg in reporter.messages)


def test_high_priority_no_request():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("critical-svc", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "critical-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {},
                                        "limits": {"cpu": "2", "memory": "1Gi"},
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
                "metrics": {},
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.priority_manager.set_priority("critical-svc", 0)
    auditor.invoke()
    assert any("no CPU request" in msg for msg in reporter.messages)
    assert any("no memory request" in msg for msg in reporter.messages)


def test_resource_utilization_summary_written():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("summary-svc", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "summary-svc",
                            "namespace": "default",
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "1", "memory": "1Gi"},
                                        "limits": {"cpu": "2", "memory": "2Gi"},
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
                    "cpu_usage": {
                        "summary-svc": [(0, 0.2), (60, 0.25), (120, 0.3)],
                    },
                    "memory_usage": {
                        "summary-svc": [
                            (0, 400_000_000.0),
                            (60, 500_000_000.0),
                            (120, 600_000_000.0),
                        ],
                    },
                },
            },
            "ResourceUtilizationAnalyzer": {
                "waste_threshold": 0.3,
                "limit_risk_threshold": 0.7,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["resource_utilization_summary"]
    assert len(summary) == 1
    assert summary[0]["service"] == "summary-svc"
    assert summary[0]["container"] == "app"
    assert summary[0]["request_cpu"] == 1.0
    assert summary[0]["limit_cpu"] == 2.0
    assert summary[0]["avg_cpu_usage"] == 0.25
    assert summary[0]["cpu_timeseries_points"] == 3
    assert summary[0]["request_memory_bytes"] == 1024**3
    assert summary[0]["limit_memory_bytes"] == 2 * 1024**3
    assert summary[0]["avg_memory_usage_bytes"] == 500_000_000.0
    assert summary[0]["memory_timeseries_points"] == 3
