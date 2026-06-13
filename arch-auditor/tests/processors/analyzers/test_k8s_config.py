from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_missing_resource_quota_warning():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "frontend",
                            "namespace": "otel-demo",
                            "containers": [
                                {
                                    "name": "frontend",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "100Mi"},
                                        "limits": {"cpu": "200m", "memory": "200Mi"},
                                    },
                                    "livenessProbe": {"tcpSocket": {"port": 8080}},
                                    "readinessProbe": {"tcpSocket": {"port": 8080}},
                                }
                            ],
                            "securityContext": {"runAsNonRoot": True},
                            "volumes": [],
                        }
                    ],
                    "pods": [],
                    "resource_quotas": [],
                },
            },
            "K8sConfigAnalyzer": {"namespaces": ["otel-demo"]},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any("MISSING_RESOURCE_QUOTA" in msg for msg in reporter.messages)


def test_host_path_mount_warning():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [
                        {
                            "name": "frontend",
                            "namespace": "otel-demo",
                            "containers": [
                                {
                                    "name": "frontend",
                                    "image": "img",
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "100Mi"},
                                        "limits": {"cpu": "200m", "memory": "200Mi"},
                                    },
                                    "livenessProbe": {"tcpSocket": {"port": 8080}},
                                    "readinessProbe": {"tcpSocket": {"port": 8080}},
                                }
                            ],
                            "securityContext": {"runAsNonRoot": True},
                            "volumes": [
                                {
                                    "name": "host-storage",
                                    "hostPath": {"path": "/var/lib/data"},
                                }
                            ],
                        }
                    ],
                    "pods": [],
                    "resource_quotas": [
                        {
                            "name": "quota",
                            "namespace": "otel-demo",
                            "hard": {"requests.cpu": "1"},
                            "used": {},
                        }
                    ],
                },
            },
            "K8sConfigAnalyzer": {"namespaces": ["otel-demo"]},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any("HOST_PATH_MOUNT" in msg for msg in reporter.messages)
