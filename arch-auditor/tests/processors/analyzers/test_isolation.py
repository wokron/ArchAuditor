from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_same_service_replicas_on_same_node_error():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "checkout-a",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "checkout"},
                        },
                        {
                            "name": "checkout-b",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "checkout"},
                        },
                    ],
                    "node_details": {
                        "node-1": {"name": "node-1", "zone": "zone-a", "labels": {}}
                    },
                },
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "checkout" in msg and "node-1" in msg and "not sufficiently isolated" in msg
        for msg in reporter.messages
    )


def test_same_service_replicas_single_zone_warning():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "payment-a",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "payment"},
                        },
                        {
                            "name": "payment-b",
                            "namespace": "default",
                            "node_name": "node-2",
                            "zone": "zone-a",
                            "labels": {"app": "payment"},
                        },
                    ],
                    "node_details": {
                        "node-1": {"name": "node-1", "zone": "zone-a", "labels": {}},
                        "node-2": {"name": "node-2", "zone": "zone-a", "labels": {}},
                    },
                },
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    assert any(
        "payment" in msg and "single availability zone" in msg
        for msg in reporter.messages
    )


def test_isolation_summary_written_for_replica_spread():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "frontend-a",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "frontend"},
                        },
                        {
                            "name": "frontend-b",
                            "namespace": "default",
                            "node_name": "node-2",
                            "zone": "zone-b",
                            "labels": {"app": "frontend"},
                        },
                    ],
                    "node_details": {
                        "node-1": {"name": "node-1", "zone": "zone-a", "labels": {}},
                        "node-2": {"name": "node-2", "zone": "zone-b", "labels": {}},
                    },
                },
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["isolation_summary"]
    assert summary["analyzed_services"] == ["frontend"]
    assert len(summary["service_zone_spread"]) == 1
    assert summary["service_zone_spread"][0]["replica_node_count"] == 2
