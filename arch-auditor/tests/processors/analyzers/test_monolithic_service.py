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
                    # hub-svc has 16 edges (in+out), exceeds threshold=15
                    ("hub-svc", "A"), ("hub-svc", "B"), ("hub-svc", "C"),
                    ("hub-svc", "D"), ("hub-svc", "E"), ("hub-svc", "F"),
                    ("hub-svc", "G"), ("hub-svc", "H"),
                    ("X", "hub-svc"), ("Y", "hub-svc"), ("Z", "hub-svc"),
                    ("W", "hub-svc"), ("V", "hub-svc"), ("U", "hub-svc"),
                    ("T", "hub-svc"), ("S", "hub-svc"),
                    # normal service
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
            "MonolithicServiceAnalyzer": {
                "degree_threshold": 15,
                "resource_multiplier": 5.0,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("hub-svc" in msg and "degree" in msg for msg in reporter.messages)


def test_monolithic_by_resource():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "normal-1",
                            "namespace": "default",
                            "containers": [
                                {"name": "app", "image": "img", "resources": {"requests": {"cpu": "100m"}}},
                            ],
                        },
                        {
                            "name": "normal-2",
                            "namespace": "default",
                            "containers": [
                                {"name": "app", "image": "img", "resources": {"requests": {"cpu": "200m"}}},
                            ],
                        },
                        {
                            "name": "normal-3",
                            "namespace": "default",
                            "containers": [
                                {"name": "app", "image": "img", "resources": {"requests": {"cpu": "150m"}}},
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
                                        "requests": {"cpu": "16", "memory": "32Gi"},
                                    },
                                }
                            ],
                        },
                    ],
                    "pods": [],
                },
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
    assert any("fat-svc" in msg and "median" in msg for msg in reporter.messages)
    # normal services should NOT trigger resource warning
    assert not any("normal-" in msg and "median" in msg for msg in reporter.messages)
