from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_manual_change_drift():
    """Manual change older than 24h → WARNING."""
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": "2026-05-20T10:00:00+00:00",
                        "changed_by": "user",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("Configuration drift detected" in msg for msg in reporter.messages)
    assert any("my-svc" in msg for msg in reporter.messages)


def test_controller_change_ignored():
    """Controller-triggered change → no alert."""
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": "2026-05-20T10:00:00+00:00",
                        "changed_by": "controller",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert len(reporter.messages) == 0


def test_recent_change_ignored():
    """Change within threshold → no alert."""
    import datetime
    recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": recent.isoformat(),
                        "changed_by": "user",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert not any("drift" in msg for msg in reporter.messages)


def test_config_drift_summary_written():
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "namespace": "default",
                        "kind": "Deployment",
                        "name": "my-svc",
                        "revision": "3",
                        "changed_at": "2026-05-20T10:00:00+00:00",
                        "changed_by": "user",
                        "reason": "manual annotation present",
                        "change_cause": "manual hotfix",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["config_drift_summary"]
    assert len(summary) == 1
    assert summary[0]["resource"] == "deployment/default/my-svc"
    assert summary[0]["is_manual_change"] is True


def test_config_drift_prefers_latest_manual_change_over_controller_followup():
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "namespace": "default",
                        "kind": "Deployment.apps",
                        "name": "my-svc",
                        "changed_at": "2026-06-13T10:00:00+00:00",
                        "changed_by": "user",
                        "manager": "kubernetes-admin",
                        "reason": "manual kubectl patch",
                        "verb": "patch",
                        "username": "kubernetes-admin",
                        "event_source": "k8s_audit_log",
                    },
                    {
                        "resource": "deployment/default/my-svc",
                        "namespace": "default",
                        "kind": "Deployment.apps",
                        "name": "my-svc",
                        "changed_at": "2026-06-13T10:05:00+00:00",
                        "changed_by": "controller",
                        "manager": "system:serviceaccount:kube-system:deployment-controller",
                        "reason": "controller reconcile",
                        "verb": "update",
                        "username": "system:serviceaccount:kube-system:deployment-controller",
                        "event_source": "k8s_audit_log",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["config_drift_summary"]
    assert len(summary) == 1
    assert summary[0]["latest_changed_by"] == "user"
    assert summary[0]["manager"] == "kubernetes-admin"
    assert summary[0]["verb"] == "patch"
    assert summary[0]["is_manual_change"] is True
    assert summary[0]["selected_event_strategy"] == "latest_manual_change"
    assert summary[0]["latest_observed_by"] == "controller"
    assert summary[0]["latest_observed_verb"] == "update"
