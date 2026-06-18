from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_slow_startup_warning():
    """Pod took > 30s to become ready → WARNING."""
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "slow-pod-1",
                            "namespace": "default",
                            "creation_time": "2026-06-02T10:00:00+00:00",
                            "ready_time": "2026-06-02T10:01:00+00:00",
                            "labels": {"app": "slow-svc"},
                        },
                    ],
                },
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [],
            },
            "MaintainabilityAnalyzer": {
                "startup_threshold_seconds": 30,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("slow-svc" in msg and "ready" in msg for msg in reporter.messages)


def test_fast_startup_silent():
    """Pod started quickly → no alert."""
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "fast-pod-1",
                            "namespace": "default",
                            "creation_time": "2026-06-02T10:00:00+00:00",
                            "ready_time": "2026-06-02T10:00:10+00:00",
                            "labels": {"app": "fast-svc"},
                        },
                    ],
                },
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [],
            },
            "MaintainabilityAnalyzer": {
                "startup_threshold_seconds": 30,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert len(reporter.messages) == 0


def test_high_rollback_ratio():
    """Rollbacks / total > 25% → WARNING."""
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {"deployments": [], "pods": []},
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {"service": "unstable", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:00:00+00:00", "success": True},
                    {"service": "unstable", "action": "rollback", "version": "v0", "deployed_at": "2026-06-01T11:00:00+00:00", "success": False},
                    {"service": "unstable", "action": "deploy", "version": "v2", "deployed_at": "2026-06-01T12:00:00+00:00", "success": True},
                    {"service": "unstable", "action": "rollback", "version": "v1", "deployed_at": "2026-06-01T13:00:00+00:00", "success": False},
                ],
            },
            "MaintainabilityAnalyzer": {
                "rollback_ratio_threshold": 0.25,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("unstable" in msg and "rollback" in msg for msg in reporter.messages)


def test_low_deploy_frequency_info():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {"deployments": [], "pods": []},
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {"service": "svc-a", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:00:00+00:00", "success": True},
                    {"service": "svc-a", "action": "deploy", "version": "v2", "deployed_at": "2026-06-02T10:00:00+00:00", "success": True},
                    {"service": "svc-a", "action": "deploy", "version": "v3", "deployed_at": "2026-06-03T10:00:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:01:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v2", "deployed_at": "2026-06-02T10:01:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v3", "deployed_at": "2026-06-03T10:01:00+00:00", "success": True},
                    {"service": "svc-c", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:02:00+00:00", "success": True},
                ],
            },
            "MaintainabilityAnalyzer": {
                "low_deploy_frequency_ratio": 0.5,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("low deployment frequency" in msg and "svc-c" in msg for msg in reporter.messages)


def test_co_deployment_info():
    """Two services always deployed together → INFO."""
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {"deployments": [], "pods": []},
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {"service": "svc-a", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:00:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:01:00+00:00", "success": True},
                    {"service": "svc-a", "action": "deploy", "version": "v2", "deployed_at": "2026-06-02T10:00:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v2", "deployed_at": "2026-06-02T10:02:00+00:00", "success": True},
                ],
            },
            "MaintainabilityAnalyzer": {
                "co_deploy_overlap_threshold": 0.8,
                "min_co_deploy_events_per_service": 2,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("deployed together" in msg and "svc-a" in msg for msg in reporter.messages)


def test_co_deployment_requires_minimum_history():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {"deployments": [], "pods": []},
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {"service": "svc-a", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:00:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:01:00+00:00", "success": True},
                ],
            },
            "MaintainabilityAnalyzer": {
                "co_deploy_overlap_threshold": 0.8,
                "min_co_deploy_events_per_service": 3,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert not any("deployed together" in msg for msg in reporter.messages)


def test_maintainability_summary_written():
    config = {
        "processors": {
            "K8sConfigSource": {
                "type": "Mock",
                "k8s_configs": {
                    "deployments": [],
                    "pods": [
                        {
                            "name": "slow-pod-1",
                            "namespace": "default",
                            "creation_time": "2026-06-02T10:00:00+00:00",
                            "ready_time": "2026-06-02T10:01:00+00:00",
                            "labels": {"app": "slow-svc"},
                        },
                    ],
                },
            },
            "DeploymentHistorySource": {
                "type": "Mock",
                "events": [
                    {"service": "svc-a", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:00:00+00:00", "success": True},
                    {"service": "svc-b", "action": "deploy", "version": "v1", "deployed_at": "2026-06-01T10:01:00+00:00", "success": True},
                ],
            },
            "MaintainabilityAnalyzer": {
                "startup_threshold_seconds": 30,
                "co_deploy_overlap_threshold": 0.8,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["maintainability_summary"]
    assert len(summary["startup_issues"]) == 1
    assert summary["startup_issues"][0]["service"] == "slow-svc"
    assert "deployment_history_source" in summary
