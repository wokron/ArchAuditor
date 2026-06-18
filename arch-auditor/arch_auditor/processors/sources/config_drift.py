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


class ConfigDriftSource(Processor):
    """Source for configuration-change audit events.

    Supported source types:
      - Mock  - explicit event list in config
      - K8s   - prefers Kubernetes audit log, falls back to Deployment revision history

    Each event is a dict with:
        resource   - identifier (e.g. "deployment/default/my-svc")
        changed_at - ISO-format timestamp
        changed_by - "controller" | "user" | "unknown"
    """

    @staticmethod
    def name() -> str:
        return "ConfigDriftSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config_dict: dict) -> bool:
        source_type = config_dict.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type
        self.manual_annotation_keys = config_dict.get(
            "manual_annotation_keys",
            [
                "kubectl.kubernetes.io/last-applied-configuration",
                "archauditor.io/manual-change",
            ],
        )
        self.manual_label_keys = config_dict.get(
            "manual_label_keys",
            [
                "archauditor.io/manual-change",
            ],
        )
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
        self.allowed_kinds = {
            str(kind).lower()
            for kind in config_dict.get(
                "audit_kinds",
                [
                    "deployment",
                    "statefulset",
                    "daemonset",
                    "configmap",
                    "secret",
                    "namespace",
                    "service",
                ],
            )
        }
        self.allowed_verbs = {
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
                return True
            except Exception as e:
                self._report_error("init", e)
                return False

        return False

    def process(self) -> None:
        if self.source_type == "Mock":
            self.context.system_state.extra_attrs["config_drift_events"] = self.events
            self.context.system_state.extra_attrs["config_drift_event_source"] = "mock"
        elif self.source_type == "K8s":
            self._process_k8s()

    # ------------------------------------------------------------------
    # K8s  - prefer audit log, fall back to Deployment revision history
    # ------------------------------------------------------------------
    def _process_k8s(self) -> None:
        audit_events = self._read_k8s_audit_events()
        if audit_events:
            self.context.system_state.extra_attrs["config_drift_events"] = audit_events
            self.context.system_state.extra_attrs["config_drift_event_source"] = (
                "k8s_audit_log"
            )
            return

        events: list[dict] = []
        for ns in self.namespaces:
            try:
                deps = self.apps_v1.list_namespaced_deployment(
                    ns, _request_timeout=self.request_timeout_seconds
                )
                for dep in deps.items:
                    name = dep.metadata.name
                    resource = f"deployment/{ns}/{name}"

                    # Collect ReplicaSets owned by this Deployment
                    label_sel = _build_selector(dep.spec.selector.match_labels)
                    if not label_sel:
                        continue
                    rs_list = self.apps_v1.list_namespaced_replica_set(
                        ns,
                        label_selector=label_sel,
                        _request_timeout=self.request_timeout_seconds,
                    )

                    for rs in rs_list.items:
                        # Keep only RS owned by this Deployment
                        owner_refs = rs.metadata.owner_references or []
                        if not any(
                            o.kind == "Deployment" and o.name == name
                            for o in owner_refs
                        ):
                            continue

                        annotations = rs.metadata.annotations or {}
                        labels = rs.metadata.labels or {}
                        manager = _latest_manager(rs.metadata.managed_fields or [])
                        change_cause = annotations.get(
                            "kubernetes.io/change-cause", ""
                        )
                        changed_by, reason = self._classify_change(
                            annotations, labels, change_cause, manager
                        )

                        created = rs.metadata.creation_timestamp
                        changed_at = _normalize_k8s_datetime(created)
                        events.append(
                            {
                                "resource": resource,
                                "namespace": ns,
                                "kind": "Deployment",
                                "name": name,
                                "revision": annotations.get(
                                    "deployment.kubernetes.io/revision", ""
                                ),
                                "changed_at": changed_at,
                                "changed_by": changed_by,
                                "reason": reason,
                                "change_cause": change_cause,
                                "manager": manager,
                                "event_source": "k8s_revision_history",
                            }
                        )
            except Exception as e:
                self._report_error(f"Deployments/{ns}", e)

        events.sort(
            key=lambda item: (
                item.get("resource", ""),
                item.get("changed_at", ""),
                item.get("revision", ""),
            )
        )
        self.context.system_state.extra_attrs["config_drift_events"] = events
        self.context.system_state.extra_attrs["config_drift_event_source"] = (
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
                event = self._audit_event_to_summary(payload)
                if event is not None:
                    events.append(event)
        except Exception as e:
            self._report_error("audit-log", e)
            return []

        events.sort(
            key=lambda item: (
                item.get("resource", ""),
                item.get("changed_at", ""),
                item.get("verb", ""),
            )
        )
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

        resource_pattern = "|".join(sorted(self._audit_resource_candidates()))
        verb_pattern = "|".join(sorted(self.allowed_verbs))
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

    def _audit_resource_candidates(self) -> set[str]:
        names: set[str] = set()
        for kind in self.allowed_kinds:
            normalized = str(kind).strip().lower()
            if not normalized:
                continue
            names.add(normalized)
            if normalized.endswith("s"):
                continue
            if normalized.endswith("y"):
                names.add(f"{normalized[:-1]}ies")
            else:
                names.add(f"{normalized}s")
        return names

    def _audit_event_to_summary(self, payload: dict) -> dict | None:
        if not isinstance(payload, dict):
            return None

        object_ref = payload.get("objectRef") or {}
        resource_kind = str(object_ref.get("resource") or "").strip().lower()
        namespace = str(object_ref.get("namespace") or "").strip()
        name = str(object_ref.get("name") or "").strip()
        api_group = str(object_ref.get("apiGroup") or "").strip()
        verb = str(payload.get("verb") or "").strip().lower()

        if not resource_kind or not name or verb not in self.allowed_verbs:
            return None

        singular_kind = resource_kind[:-1] if resource_kind.endswith("s") else resource_kind
        if resource_kind not in self.allowed_kinds and singular_kind not in self.allowed_kinds:
            return None

        user_info = payload.get("user") or {}
        username = str(user_info.get("username") or "").strip()
        user_agent = str(payload.get("userAgent") or "").strip()
        source_ips = payload.get("sourceIPs") or []
        source_ip = source_ips[0] if source_ips else None
        changed_at = (
            payload.get("stageTimestamp")
            or payload.get("requestReceivedTimestamp")
            or ""
        )
        changed_at = _normalize_audit_timestamp(changed_at)

        changed_by, reason = self._classify_audit_actor(username, user_agent)
        kind_label = singular_kind.capitalize()
        if api_group:
            kind_label = f"{kind_label}.{api_group}"
        resource = f"{singular_kind}/{namespace or '_cluster'}/{name}"

        revision = _extract_revision_from_audit_payload(payload)

        return {
            "resource": resource,
            "namespace": namespace or None,
            "kind": kind_label,
            "name": name,
            "revision": revision,
            "changed_at": changed_at,
            "changed_by": changed_by,
            "reason": reason,
            "change_cause": "",
            "manager": username or user_agent,
            "verb": verb,
            "username": username or None,
            "user_agent": user_agent or None,
            "source_ip": source_ip,
            "event_source": "k8s_audit_log",
        }

    def _classify_change(
        self, annotations: dict, labels: dict, change_cause: str, manager: str
    ) -> tuple[str, str]:
        for key in self.manual_annotation_keys:
            if key in annotations:
                return "user", f"manual annotation '{key}' present"

        for key in self.manual_label_keys:
            if key in labels:
                return "user", f"manual label '{key}' present"

        if change_cause:
            if _is_controller_change(change_cause):
                return "controller", "controller-like change-cause"
            if _is_manual_change(change_cause):
                return "user", "manual-looking change-cause"

        if manager:
            if _is_controller_manager(manager):
                return "controller", f"controller-like manager '{manager}'"
            if _is_manual_manager(manager):
                return "user", f"manual-looking manager '{manager}'"

        return "unknown", "insufficient audit metadata"

    def _classify_audit_actor(self, username: str, user_agent: str) -> tuple[str, str]:
        lower_username = username.lower()
        lower_agent = user_agent.lower()

        if (
            lower_username.startswith("system:")
            or "controller" in lower_username
            or "operator" in lower_username
            or "controller" in lower_agent
            or "operator" in lower_agent
        ):
            return "controller", f"controller-like audit actor '{username or user_agent}'"

        if (
            "kubectl" in lower_agent
            or lower_username.startswith("kubernetes-admin")
            or lower_username.startswith("admin")
            or lower_username.startswith("user:")
        ):
            return "user", f"manual-looking audit actor '{username or user_agent}'"

        if username:
            return "unknown", f"unclassified audit actor '{username}'"
        return "unknown", "missing audit actor metadata"

    def _report_error(self, ctx: str, error) -> None:
        reason = getattr(error, "reason", str(error))
        self.context.reporter.report(
            ReportMessage(
                self.name(),
                ReportType.WARNING,
                f"ConfigDriftSource {ctx}: {reason}",
            )
        )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _is_controller_change(change_cause: str) -> bool:
    """Heuristic: does the change-cause look like it came from a controller?"""
    lower = change_cause.lower()
    controller_keywords = (
        "hpa",
        "horizontal-pod-autoscaler",
        "vpa",
        "cronjob",
        "controller",
        "operator",
        "reconciler",
        "gitops",
        "argo",
        "flux",
    )
    return any(k in lower for k in controller_keywords)


def _is_manual_change(change_cause: str) -> bool:
    lower = change_cause.lower()
    manual_keywords = (
        "manual",
        "kubectl edit",
        "kubectl-edit",
        "kubectl patch",
        "kubectl-patch",
        "hotfix",
    )
    return any(k in lower for k in manual_keywords)


def _is_controller_manager(manager: str) -> bool:
    lower = manager.lower()
    controller_keywords = (
        "controller",
        "operator",
        "reconciler",
        "helm",
        "argocd",
        "argo",
        "flux",
        "kustomize",
        "terraform",
        "replicaset-controller",
        "deployment-controller",
    )
    return any(k in lower for k in controller_keywords)


def _is_manual_manager(manager: str) -> bool:
    lower = manager.lower()
    manual_keywords = (
        "kubectl-edit",
        "kubectl-edit-manager",
        "kubectl-patch",
        "kubectl-rollout",
    )
    return any(k in lower for k in manual_keywords)


def _latest_manager(managed_fields: list) -> str:
    latest_time = None
    latest_manager = ""
    for field in managed_fields or []:
        manager = getattr(field, "manager", "") or ""
        timestamp = getattr(field, "time", None)
        if latest_time is None or (timestamp and timestamp >= latest_time):
            latest_time = timestamp or latest_time
            latest_manager = manager
    return latest_manager


def _build_selector(match_labels: dict | None) -> str:
    """Convert matchLabels dict to a label selector string."""
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
