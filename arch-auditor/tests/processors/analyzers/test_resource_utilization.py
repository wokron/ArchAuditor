from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_wasting_cpu():
    """Service uses < 30% of request → WARNING."""
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
                            (0, 0.1), (60, 0.15), (120, 0.12),
                            (180, 0.1), (240, 0.11),
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


def test_limit_risk():
    """Short-term avg > 70% of limit → WARNING."""
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
                            (0, 1.5), (60, 1.5),
                            (120, 1.5), (180, 1.5), (240, 1.5),
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
    assert any("stability risk" in msg for msg in reporter.messages)


def test_high_priority_no_request():
    """P0 service without CPU request → ERROR."""
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
