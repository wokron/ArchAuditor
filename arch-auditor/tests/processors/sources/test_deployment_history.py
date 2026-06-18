import json
from pathlib import Path

from arch_auditor.arch_auditor import ArchAuditor


class DummyReporter:
    def report(self, _msg):
        pass


def test_deployment_history_source_prefers_audit_log(tmp_path: Path):
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
        "requestObject": {
            "metadata": {
                "annotations": {
                    "deployment.kubernetes.io/revision": "12",
                }
            }
        },
    }
    audit_log.write_text(json.dumps(audit_event) + "\n", encoding="utf-8")

    config = {
        "processors": {
            "DeploymentHistorySource": {
                "type": "K8s",
                "audit_log_path": str(audit_log),
            },
        }
    }

    auditor = ArchAuditor(config, reporter=DummyReporter())
    auditor.invoke()

    history = auditor.system_state.extra_attrs["deployment_history"]
    assert len(history) == 1
    assert history[0]["service"] == "frontend"
    assert history[0]["action"] == "deploy"
    assert history[0]["version"] == "12"
    assert history[0]["event_source"] == "k8s_audit_log"
    assert (
        auditor.system_state.extra_attrs["deployment_history_source"]
        == "k8s_audit_log"
    )


def test_deployment_history_ignores_status_subresource_updates(tmp_path: Path):
    audit_log = tmp_path / "audit.log"
    audit_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "verb": "update",
                        "stageTimestamp": "2026-06-13T10:00:00Z",
                        "user": {"username": "system:serviceaccount:kube-system:deployment-controller"},
                        "objectRef": {
                            "resource": "deployments",
                            "namespace": "default",
                            "name": "frontend",
                            "apiGroup": "apps",
                            "subresource": "status",
                        },
                    }
                ),
                json.dumps(
                    {
                        "verb": "patch",
                        "stageTimestamp": "2026-06-13T10:01:00Z",
                        "user": {"username": "kubernetes-admin"},
                        "objectRef": {
                            "resource": "deployments",
                            "namespace": "default",
                            "name": "frontend",
                            "apiGroup": "apps",
                        },
                        "requestObject": {
                            "metadata": {
                                "resourceVersion": "12",
                                "annotations": {
                                    "kubernetes.io/change-cause": "manual rollout"
                                },
                            }
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    config = {
        "processors": {
            "DeploymentHistorySource": {
                "type": "K8s",
                "audit_log_path": str(audit_log),
                "namespaces": ["default"],
            }
        }
    }
    auditor = ArchAuditor(config)
    auditor.invoke()

    events = auditor.system_state.extra_attrs["deployment_history"]
    assert len(events) == 1
    assert events[0]["service"] == "frontend"
    assert events[0]["verb"] == "patch"
    assert events[0]["version"] == "12"


def test_deployment_history_version_falls_back_to_resource_version(tmp_path: Path):
    audit_log = tmp_path / "audit.log"
    audit_event = {
        "verb": "patch",
        "stageTimestamp": "2026-06-13T10:01:00Z",
        "user": {"username": "kubernetes-admin"},
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
            "DeploymentHistorySource": {
                "type": "K8s",
                "audit_log_path": str(audit_log),
                "namespaces": ["default"],
            }
        }
    }
    auditor = ArchAuditor(config)
    auditor.invoke()

    events = auditor.system_state.extra_attrs["deployment_history"]
    assert len(events) == 1
    assert events[0]["version"] == "34567"
