import json
from pathlib import Path

from arch_auditor.arch_auditor import ArchAuditor


class DummyReporter:
    def report(self, _msg):
        pass


def test_config_drift_source_prefers_audit_log(tmp_path: Path):
    audit_log = tmp_path / "audit.log"
    audit_event = {
        "verb": "patch",
        "user": {
            "username": "kubernetes-admin",
        },
        "userAgent": "kubectl/v1.35",
        "sourceIPs": ["127.0.0.1"],
        "requestReceivedTimestamp": "2026-06-13T12:00:00Z",
        "objectRef": {
            "resource": "deployments",
            "namespace": "default",
            "name": "frontend",
            "apiGroup": "apps",
        },
    }
    audit_log.write_text(json.dumps(audit_event) + "\n", encoding="utf-8")

    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "K8s",
                "audit_log_path": str(audit_log),
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }

    auditor = ArchAuditor(config, reporter=DummyReporter())
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["config_drift_summary"]
    assert len(summary) == 1
    assert summary[0]["resource"] == "deployment/default/frontend"
    assert summary[0]["event_source"] == "k8s_audit_log"
    assert summary[0]["verb"] == "patch"
    assert summary[0]["username"] == "kubernetes-admin"
    assert summary[0]["is_manual_change"] is True


def test_config_drift_revision_falls_back_to_resource_version(tmp_path: Path):
    audit_log = tmp_path / "audit.log"
    audit_event = {
        "verb": "patch",
        "user": {"username": "kubernetes-admin"},
        "requestReceivedTimestamp": "2026-06-13T12:00:00Z",
        "objectRef": {
            "resource": "deployments",
            "namespace": "default",
            "name": "frontend",
            "apiGroup": "apps",
            "resourceVersion": "34567",
        },
    }
    audit_log.write_text(json.dumps(audit_event) + "\n", encoding="utf-8")

    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "K8s",
                "audit_log_path": str(audit_log),
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }

    auditor = ArchAuditor(config, reporter=DummyReporter())
    auditor.invoke()

    summary = auditor.system_state.extra_attrs["config_drift_summary"]
    assert summary[0]["revision"] == "34567"
