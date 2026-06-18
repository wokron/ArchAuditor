from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
import json
from pathlib import Path
import shlex
import subprocess
import datetime

from .k8s_config import _load_kube_config_resilient

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException

    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class DeploymentHistorySource(Processor):
    """Source for deployment / rollback history.

    Supported source types:
      - Mock  - explicit event list in config
      - K8s   - prefers Kubernetes audit log, falls back to ReplicaSet history

    Each event is a dict with:
        service        - logical workload name
        namespace      - Kubernetes namespace
        resource       - e.g. deployment/default/frontend
        action         - deploy | rollback
        version        - revision/version string if available
        deployed_at    - ISO timestamp
        success        - whether the action is considered successful
        event_source   - k8s_audit_log | k8s_revision_history | mock
    """

    @staticmethod
    def name() -> str:
        return "DeploymentHistorySource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config_dict: dict) -> bool:
        source_type = config_dict.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type
        self.audit_log_path = config_dict.get(
            "audit_log_path", "/var/log/kubernetes/audit.log"
        )
        self.audit_log_container = config_dict.get("audit_log_container")
        self.audit_log_read_mode = str(
            config_dict.get("audit_log_read_mode", "auto")
        ).lower()
        self.audit_log_tail_lines = int(config_dict.get("audit_log_tail_lines", 5000))
        self.audit_log_timeout_seconds = int(
            config_dict.get("audit_log_timeout_seconds", 15)
        )
        self.request_timeout_seconds = int(
            config_dict.get("request_timeout_seconds", 10)
        )
        self.audit_resources = {
            str(resource).lower()
            for resource in config_dict.get(
                "audit_resources",
                ["deployments", "statefulsets", "daemonsets"],
            )
        }
        self.audit_verbs = {
            str(verb).lower()
            for verb in config_dict.get(
                "audit_verbs",
                ["create", "update", "patch", "delete"],
            )
        }

        if source_type == "Mock":
            self.events: list[dict] = config_dict.get("events", [])
            return True

        if source_type == "K8s":
            if not K8S_AVAILABLE:
                return False
            try:
                kubeconfig_path = config_dict.get("kubeconfig", None)
                if kubeconfig_path:
                    _load_kube_config_resilient(kubeconfig_path)
                else:
                    try:
                        config.load_incluster_config()
                    except config.ConfigException:
                        _load_kube_config_resilient()
                self.apps_v1 = client.AppsV1Api()
                self.namespaces = config_dict.get(
                    "namespaces",
                    [config_dict.get("namespace", "default")],
                )
                self.rollback_window_min = config_dict.get(
                    "rollback_window_minutes", 10
                )
                return True
            except Exception as e:
                self._report_error("init", e)
                return False

        return False

    def process(self) -> None:
        if self.source_type == "Mock":
            self.context.system_state.extra_attrs["deployment_history"] = self.events
            self.context.system_state.extra_attrs["deployment_history_source"] = "mock"
        elif self.source_type == "K8s":
            self._process_k8s()

    def _process_k8s(self) -> None:
        audit_events = self._read_k8s_audit_events()
        if audit_events:
            self.context.system_state.extra_attrs["deployment_history"] = audit_events
            self.context.system_state.extra_attrs["deployment_history_source"] = (
                "k8s_audit_log"
            )
            return

        events = self._read_replicaset_history()
        self.context.system_state.extra_attrs["deployment_history"] = events
        self.context.system_state.extra_attrs["deployment_history_source"] = (
            "k8s_revision_history"
        )

    def _read_k8s_audit_events(self) -> list[dict]:
        events = []
        try:
            for line in self._iter_audit_log_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = self._audit_event_to_history(payload)
                if event is not None:
                    events.append(event)
        except Exception as e:
            self._report_error("audit-log", e)
            return []

        events.sort(
            key=lambda item: (
                item.get("namespace", ""),
                item.get("service", ""),
                item.get("deployed_at", ""),
            )
        )
        return events

    def _read_replicaset_history(self) -> list[dict]:
        events: list[dict] = []

        for ns in self.namespaces:
            try:
                deps = self.apps_v1.list_namespaced_deployment(
                    ns, _request_timeout=self.request_timeout_seconds
                )
                for dep in deps.items:
                    svc_name = dep.metadata.name
                    label_sel = _build_selector(dep.spec.selector.match_labels)
                    if not label_sel:
                        continue
                    rs_list = self.apps_v1.list_namespaced_replica_set(
                        ns,
                        label_selector=label_sel,
                        _request_timeout=self.request_timeout_seconds,
                    )

                    rs_items = sorted(
                        rs_list.items,
                        key=lambda r: r.metadata.creation_timestamp or "",
                    )

                    prev_ts = None
                    for rs in rs_items:
                        owner_refs = rs.metadata.owner_references or []
                        if not any(
                            o.kind == "Deployment" and o.name == svc_name
                            for o in owner_refs
                        ):
                            continue

                        annotations = rs.metadata.annotations or {}
                        revision = annotations.get(
                            "deployment.kubernetes.io/revision", ""
                        )
                        created = rs.metadata.creation_timestamp
                        deployed_at = _normalize_k8s_datetime(created)

                        action = "deploy"
                        success = True
                        rollback_from = None
                        rollback_to = None
                        if prev_ts and created:
                            delta = (created - prev_ts).total_seconds() / 60.0
                            if delta < self.rollback_window_min:
                                action = "rollback"
                                success = False
                                rollback_to = revision or None

                        events.append(
                            {
                                "service": svc_name,
                                "namespace": ns,
                                "resource": f"deployment/{ns}/{svc_name}",
                                "action": action,
                                "version": revision,
                                "deployed_at": deployed_at,
                                "success": success,
                                "rollback_from": rollback_from,
                                "rollback_to": rollback_to,
                                "verb": "update",
                                "username": None,
                                "user_agent": None,
                                "source_ip": None,
                                "event_source": "k8s_revision_history",
                            }
                        )
                        prev_ts = created

            except Exception as e:
                self._report_error(f"Deployments/{ns}", e)

        return events

    def _iter_audit_log_lines(self):
        path = Path(self.audit_log_path)
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                yield from f
            return

        for command in self._audit_log_read_attempts():
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.audit_log_timeout_seconds,
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                yield line
            return

    def _audit_log_read_attempts(self) -> list[list[str]]:
        shell_command = self._build_audit_log_shell_command()
        attempts: list[list[str]] = []

        if self.audit_log_container and self.audit_log_read_mode in {
            "auto",
            "minikube_ssh",
        }:
            attempts.append(
                [
                    "minikube",
                    "ssh",
                    f"sudo sh -lc {shlex.quote(shell_command)}",
                ]
            )

        if self.audit_log_container and self.audit_log_read_mode in {
            "auto",
            "docker_exec",
        }:
            attempts.append(
                [
                    "docker",
                    "exec",
                    self.audit_log_container,
                    "sh",
                    "-lc",
                    shell_command,
                ]
            )

        return attempts

    def _build_audit_log_shell_command(self) -> str:
        log_target = self._build_audit_log_glob_target()
        if self.audit_log_tail_lines > 0:
            base_command = (
                f"tail -n {self.audit_log_tail_lines} {log_target} 2>/dev/null"
            )
        else:
            base_command = f"cat {log_target} 2>/dev/null"

        resource_pattern = "|".join(sorted(self.audit_resources))
        verb_pattern = "|".join(sorted(self.audit_verbs))
        filters = [
            (
                "grep -E "
                + shlex.quote(
                    rf'"resource"[[:space:]]*:[[:space:]]*"({resource_pattern})"'
                )
            ),
            (
                "grep -E "
                + shlex.quote(
                    rf'"verb"[[:space:]]*:[[:space:]]*"({verb_pattern})"'
                )
            ),
            (
                "grep -Ev "
                + shlex.quote(r'"subresource"[[:space:]]*:[[:space:]]*"status"')
            ),
        ]
        return " | ".join([base_command, *filters]) + " || true"

    def _build_audit_log_glob_target(self) -> str:
        raw_path = str(self.audit_log_path or "").strip() or "/var/log/kubernetes/audit.log"
        if any(token in raw_path for token in ["*", "?", "["]):
            return raw_path

        path = Path(raw_path)
        if path.name == "audit.log":
            return str(path.with_name("audit*.log")).replace("\\", "/")

        return shlex.quote(raw_path)

    def _audit_event_to_history(self, payload: dict) -> dict | None:
        if not isinstance(payload, dict):
            return None

        object_ref = payload.get("objectRef") or {}
        resource = str(object_ref.get("resource") or "").strip().lower()
        namespace = str(object_ref.get("namespace") or "").strip()
        name = str(object_ref.get("name") or "").strip()
        verb = str(payload.get("verb") or "").strip().lower()
        api_group = str(object_ref.get("apiGroup") or "").strip()
        subresource = str(object_ref.get("subresource") or "").strip().lower()

        if (
            resource not in self.audit_resources
            or verb not in self.audit_verbs
            or subresource == "status"
            or not namespace
            or not name
        ):
            return None

        deployed_at = (
            payload.get("stageTimestamp")
            or payload.get("requestReceivedTimestamp")
            or ""
        )
        deployed_at = _normalize_audit_timestamp(deployed_at)
        if not deployed_at:
            return None

        request_object = payload.get("requestObject")
        response_object = payload.get("responseObject")
        metadata = {}
        if isinstance(request_object, dict):
            metadata = request_object.get("metadata") or {}
        if not metadata and isinstance(response_object, dict):
            metadata = response_object.get("metadata") or {}

        annotations = metadata.get("annotations", {}) if isinstance(metadata, dict) else {}
        revision = _extract_revision_from_audit_payload(payload)
        change_cause = annotations.get("kubernetes.io/change-cause", "")
        username = str((payload.get("user") or {}).get("username") or "").strip() or None
        user_agent = str(payload.get("userAgent") or "").strip() or None
        source_ips = payload.get("sourceIPs") or []
        source_ip = source_ips[0] if source_ips else None

        if username and (
            "deployment-controller" in username
            or "replicaset-controller" in username
            or "statefulset-controller" in username
            or "daemonset-controller" in username
        ):
            if verb == "update" and subresource == "status":
                return None

        action = "deploy"
        success = True
        rollback_from = None
        rollback_to = None
        lower_cause = str(change_cause).lower()
        if "rollback" in lower_cause or "undo" in lower_cause:
            action = "rollback"
            success = False
            rollback_to = revision or None

        singular = resource[:-1] if resource.endswith("s") else resource
        if api_group:
            workload_kind = f"{singular}.{api_group}"
        else:
            workload_kind = singular

        return {
            "service": name,
            "namespace": namespace,
            "resource": f"{singular}/{namespace}/{name}",
            "kind": workload_kind,
            "action": action,
            "version": revision,
            "deployed_at": deployed_at,
            "success": success,
            "rollback_from": rollback_from,
            "rollback_to": rollback_to,
            "verb": verb,
            "username": username,
            "user_agent": user_agent,
            "source_ip": source_ip,
            "event_source": "k8s_audit_log",
            "change_cause": change_cause,
        }

    def _report_error(self, ctx: str, error) -> None:
        reason = getattr(error, "reason", str(error))
        self.context.reporter.report(
            ReportMessage(
                self.name(),
                ReportType.WARNING,
                f"DeploymentHistorySource {ctx}: {reason}",
            )
        )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


def _build_selector(match_labels: dict | None) -> str:
    if not match_labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in match_labels.items())


def _extract_revision_from_audit_payload(payload: dict) -> str:
    object_ref = payload.get("objectRef") or {}
    request_object = payload.get("requestObject")
    response_object = payload.get("responseObject")

    candidates = []
    if isinstance(request_object, dict):
        candidates.append(request_object.get("metadata") or {})
    if isinstance(response_object, dict):
        candidates.append(response_object.get("metadata") or {})
    candidates.append(object_ref)

    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        annotations = metadata.get("annotations") or {}
        if isinstance(annotations, dict):
            revision = annotations.get("deployment.kubernetes.io/revision")
            if revision:
                return str(revision)
        resource_version = metadata.get("resourceVersion")
        if resource_version:
            return str(resource_version)

    return ""


def _normalize_audit_timestamp(timestamp: str) -> str:
    if not timestamp:
        return ""

    try:
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    else:
        parsed = parsed.astimezone(datetime.timezone.utc)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if parsed - now_utc > datetime.timedelta(minutes=5):
        local_offset = datetime.datetime.now().astimezone().utcoffset()
        if local_offset is not None:
            parsed = parsed - local_offset

    return parsed.astimezone(datetime.timezone.utc).isoformat()


def _normalize_k8s_datetime(value) -> str:
    if value is None:
        return ""
    return _normalize_audit_timestamp(value.isoformat())
