from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class DeploymentHistorySource(Processor):
    """Source for deployment/rollback event history.

    Supported source types:
      - Mock  – explicit event list in config
      - K8s   – reads ReplicaSet history from the cluster

    Each event is a dict with:
        service       - service name
        action        - "deploy" | "rollback"
        version       - version string (e.g. "v1.2.3")
        deployed_at   - ISO-format timestamp
        success       - True if deployment succeeded without rollback
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
        elif self.source_type == "K8s":
            self._process_k8s()

    # ------------------------------------------------------------------
    # K8s  –  build deploy / rollback timeline from ReplicaSets
    # ------------------------------------------------------------------
    def _process_k8s(self) -> None:
        events: list[dict] = []

        for ns in self.namespaces:
            try:
                deps = self.apps_v1.list_namespaced_deployment(ns)
                for dep in deps.items:
                    svc_name = dep.metadata.name

                    # Get ReplicaSets owned by this Deployment
                    label_sel = _build_selector(dep.spec.selector.match_labels)
                    if not label_sel:
                        continue
                    rs_list = self.apps_v1.list_namespaced_replica_set(
                        ns, label_selector=label_sel
                    )

                    # Sort RS by creation time (oldest first)
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
                        deployed_at = created.isoformat() if created else ""

                        # Determine if this was a rollback
                        action = "deploy"
                        success = True
                        if prev_ts and created:
                            delta = (created - prev_ts).total_seconds() / 60.0
                            if delta < self.rollback_window_min:
                                action = "rollback"
                                success = False

                        events.append({
                            "service": svc_name,
                            "action": action,
                            "version": revision,
                            "deployed_at": deployed_at,
                            "success": success,
                        })
                        prev_ts = created

            except ApiException as e:
                self._report_error(f"Deployments/{ns}", e)

        self.context.system_state.extra_attrs["deployment_history"] = events

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
