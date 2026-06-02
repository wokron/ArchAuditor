from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_p0_same_node_error():
    """Two different P0 pods on the same node → ERROR."""
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("svc-a", "svc-b")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "svc-a-pod1",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "svc-a"},
                        },
                        {
                            "name": "svc-b-pod1",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "svc-b"},
                        },
                    ],
                    "node_zones": {"node-1": "zone-a"},
                },
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.priority_manager.set_priority("svc-a", 0)
    auditor.priority_manager.set_priority("svc-b", 0)
    auditor.invoke()
    assert any("co-located" in msg and "node-1" in msg for msg in reporter.messages)


def test_p0_single_zone_warning():
    """All replicas of a P0 service in one zone → WARNING."""
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("svc-x", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "svc-x-pod1",
                            "namespace": "default",
                            "node_name": "node-1",
                            "zone": "zone-a",
                            "labels": {"app": "svc-x"},
                        },
                        {
                            "name": "svc-x-pod2",
                            "namespace": "default",
                            "node_name": "node-2",
                            "zone": "zone-a",
                            "labels": {"app": "svc-x"},
                        },
                    ],
                    "node_zones": {"node-1": "zone-a", "node-2": "zone-a"},
                },
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.priority_manager.set_priority("svc-x", 0)
    auditor.invoke()
    assert any("single" in msg and "zone" in msg for msg in reporter.messages)


def test_no_p0_services_silent():
    """No P0 services → no isolation warnings."""
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("A", "B")],
            },
            "ServicePrioritySource": {"type": "InMemory"},
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {"deployments": [], "pods": []},
            },
            "IsolationAnalyzer": {},
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert len(reporter.messages) == 0
