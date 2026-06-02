from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class ConfigDriftSource(Processor):
    """Source for configuration-change audit events.

    Supported source types:
      - Mock  – explicit event list in config
      - K8s   – inspects Deployment revision history for manual changes

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

        if source_type == "Mock":
            self.events: list[dict] = config_dict.get("events", [])
            return True

        if source_type == "K8s":
            if not K8S_AVAILABLE:
                return False
            try:
                kubeconfig_path = config_dict.get("kubeconfig", None)
                if kubeconfig_path:
                    config.load_kube_config(config_file=kubeconfig_path)
                else:
                    try:
                        config.load_incluster_config()
                    except config.ConfigException:
                        config.load_kube_config()
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
        elif self.source_type == "K8s":
            self._process_k8s()

    # ------------------------------------------------------------------
    # K8s  –  scan Deployment revision history for manual changes
    # ------------------------------------------------------------------
    def _process_k8s(self) -> None:
        events: list[dict] = []
        for ns in self.namespaces:
            try:
                deps = self.apps_v1.list_namespaced_deployment(ns)
                for dep in deps.items:
                    name = dep.metadata.name
                    resource = f"deployment/{ns}/{name}"

                    # Collect ReplicaSets owned by this Deployment
                    label_sel = _build_selector(dep.spec.selector.match_labels)
                    if not label_sel:
                        continue
                    rs_list = self.apps_v1.list_namespaced_replica_set(
                        ns, label_selector=label_sel
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
                        changed_by = annotations.get(
                            "kubernetes.io/change-cause", ""
                        )
                        if not changed_by:
                            # No change-cause → likely a manual kubectl apply
                            changed_by = "user"
                        elif _is_controller_change(changed_by):
                            changed_by = "controller"
                        else:
                            changed_by = "user"

                        created = rs.metadata.creation_timestamp
                        changed_at = (
                            created.isoformat() if created else ""
                        )
                        events.append({
                            "resource": resource,
                            "changed_at": changed_at,
                            "changed_by": changed_by,
                        })
            except ApiException as e:
                self._report_error(f"Deployments/{ns}", e)

        self.context.system_state.extra_attrs["config_drift_events"] = events

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
        "hpa", "horizontal-pod-autoscaler", "vpa",
        "cronjob", "controller", "operator", "reconciler",
        "gitops", "argo", "flux",
    )
    return any(k in lower for k in controller_keywords)


def _build_selector(match_labels: dict | None) -> str:
    """Convert matchLabels dict to a label selector string."""
    if not match_labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in match_labels.items())
