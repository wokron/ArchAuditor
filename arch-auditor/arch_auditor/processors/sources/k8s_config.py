from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class K8sConfigSource(Processor):
    
    @staticmethod
    def name() -> str:
        return "K8sConfigSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config_dict: dict) -> bool:
        self.k8s_config = config_dict or {}
        source_type = self.k8s_config.get("type", "K8s")
        self.source_type = source_type
        self.namespaces = self.k8s_config.get(
            "namespaces", 
            [self.k8s_config.get("namespace", "default")]
        )

        if source_type == "Mock":
            return True

        if not K8S_AVAILABLE:
            return False
        
        try:
            kubeconfig_path = self.k8s_config.get("kubeconfig", None)
            if kubeconfig_path:
                config.load_kube_config(config_file=kubeconfig_path)
            else:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()
            
            self.v1 = client.CoreV1Api()
            self.apps_v1 = client.AppsV1Api()
            self.autoscaling_v1 = client.AutoscalingV1Api()
            return True
            
        except Exception as e:
            self.context.reporter.report(ReportMessage(
                self.name(),
                ReportType.ERROR,
                f"Failed to init K8s : {e}"
            ))
            return False

    def process(self) -> None:
        if self.source_type == "Mock":
            # Inject k8s_configs directly from config (for testing)
            mock_data = self.k8s_config.get("k8s_configs", {})
            self.context.system_state.extra_attrs["k8s_configs"] = mock_data
            return

        k8s_configs = {
            "deployments": [],
            "services": [],
            "pods": [],
            "configmaps": [],
            "secrets": [],
            "hpa": [],
            "resource_quotas": [],
            "node_zones": {},
        }

        # Collect node -> zone mapping first (cluster-wide, not per namespace)
        self._fetch_nodes(k8s_configs)

        for ns in self.namespaces:
            self._fetch_namespace_resources(ns, k8s_configs)
        
        self.context.system_state.extra_attrs["k8s_configs"] = k8s_configs

    def _fetch_namespace_resources(self, ns: str, k8s_configs: dict) -> None:
        self._fetch_deployments(ns, k8s_configs)
        self._fetch_services(ns, k8s_configs)
        self._fetch_pods(ns, k8s_configs)
        self._fetch_configmaps(ns, k8s_configs)
        self._fetch_secrets(ns, k8s_configs)
        self._fetch_hpa(ns, k8s_configs)
        self._fetch_resource_quotas(ns, k8s_configs)

    def _fetch_deployments(self, ns: str, k8s_configs: dict) -> None:
        try:
            deps = self.apps_v1.list_namespaced_deployment(ns)
            for dep in deps.items:
                pod_spec = dep.spec.template.spec
                containers = []
                for c in (pod_spec.containers or []):
                    info = {"name": c.name, "image": c.image}
                    if c.resources:
                        info["resources"] = {
                            "limits": dict(c.resources.limits or {}),
                            "requests": dict(c.resources.requests or {}),
                        }
                    # Expose probe config so K8sConfigAnalyzer can check liveness/readiness
                    if c.liveness_probe:
                        info["livenessProbe"] = self._probe_to_dict(c.liveness_probe)
                    if c.readiness_probe:
                        info["readinessProbe"] = self._probe_to_dict(c.readiness_probe)
                    containers.append(info)

                # Expose pod-level security_context for K8sConfigAnalyzer
                sec_ctx = {}
                if pod_spec.security_context:
                    pod_security_context = pod_spec.security_context
                    sec_ctx = {
                        "run_as_non_root": getattr(
                            pod_security_context, "run_as_non_root", None
                        ),
                    }
                    container_security_contexts = [
                        c.security_context
                        for c in (pod_spec.containers or [])
                        if getattr(c, "security_context", None)
                    ]
                    if container_security_contexts:
                        first_container_sec_ctx = container_security_contexts[0]
                        sec_ctx["privileged"] = getattr(
                            first_container_sec_ctx, "privileged", None
                        )
                        sec_ctx["allow_privilege_escalation"] = getattr(
                            first_container_sec_ctx, "allow_privilege_escalation", None
                        )
                        sec_ctx["read_only_root_filesystem"] = getattr(
                            first_container_sec_ctx, "read_only_root_filesystem", None
                        )
                    if container_security_contexts and getattr(
                        container_security_contexts[0], "capabilities", None
                    ):
                        capabilities = container_security_contexts[0].capabilities
                        sec_ctx["capabilities"] = {
                            "add": list(capabilities.add or []),
                            "drop": list(capabilities.drop or []),
                        }

                # Expose volumes for hostPath mount detection
                volumes = []
                for v in (pod_spec.volumes or []):
                    vol_info = {"name": v.name}
                    if v.host_path:
                        vol_info["hostPath"] = {"path": v.host_path.path}
                    if v.empty_dir:
                        vol_info["emptyDir"] = {}
                    volumes.append(vol_info)

                k8s_configs["deployments"].append({
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "replicas": dep.spec.replicas,
                    "ready_replicas": dep.status.ready_replicas or 0,
                    "containers": containers,
                    "labels": dict(dep.metadata.labels or {}),
                    "securityContext": sec_ctx,
                    "volumes": volumes,
                })
        except ApiException as e:
            self._report_error("Deployments", ns, e)

    @staticmethod
    def _probe_to_dict(probe) -> dict:
        """Convert a Kubernetes V1Probe object to a plain dict for downstream consumers."""
        d = {}
        if probe.http_get:
            d["httpGet"] = {
                "path": probe.http_get.path,
                "port": probe.http_get.port,
            }
        if probe.tcp_socket:
            d["tcpSocket"] = {"port": probe.tcp_socket.port}
        # Kubernetes Python client versions expose exec probes as either
        # `_exec` or `exec_`, so support both to avoid runtime crashes.
        exec_action = getattr(probe, "_exec", None)
        if exec_action is None:
            exec_action = getattr(probe, "exec_", None)
        if exec_action:
            d["exec"] = {"command": list(exec_action.command or [])}
        d["initialDelaySeconds"] = probe.initial_delay_seconds
        d["periodSeconds"] = probe.period_seconds
        return d

    def _fetch_services(self, ns: str, k8s_configs: dict) -> None:
        try:
            svcs = self.v1.list_namespaced_service(ns)
            for svc in svcs.items:
                k8s_configs["services"].append({
                    "name": svc.metadata.name,
                    "namespace": svc.metadata.namespace,
                    "type": svc.spec.type,
                    "cluster_ip": svc.spec.cluster_ip,
                    "ports": [{"port": p.port, "target_port": p.target_port, "protocol": p.protocol} 
                              for p in (svc.spec.ports or [])],
                    "selector": dict(svc.spec.selector or {}),
                })
        except ApiException as e:
            self._report_error("Services", ns, e)

    def _fetch_pods(self, ns: str, k8s_configs: dict) -> None:
        try:
            pods = self.v1.list_namespaced_pod(ns)
            node_zones = k8s_configs.get("node_zones", {})
            for pod in pods.items:
                restart_count = sum(
                    cs.restart_count for cs in (pod.status.container_statuses or []) 
                    if cs.restart_count
                )
                node_name = pod.spec.node_name or ""
                zone = node_zones.get(node_name, "")

                # Extract creation / ready timestamps for maintainability analysis
                creation_time = ""
                ready_time = ""
                if pod.metadata.creation_timestamp:
                    creation_time = pod.metadata.creation_timestamp.isoformat()
                if pod.status.conditions:
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status == "True":
                            if cond.last_transition_time:
                                ready_time = cond.last_transition_time.isoformat()
                            break

                k8s_configs["pods"].append({
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node_name": node_name,
                    "zone": zone,
                    "restart_count": restart_count,
                    "creation_time": creation_time,
                    "ready_time": ready_time,
                    "labels": dict(pod.metadata.labels or {}),
                })
        except ApiException as e:
            self._report_error("Pods", ns, e)

    def _fetch_configmaps(self, ns: str, k8s_configs: dict) -> None:
        try:
            cms = self.v1.list_namespaced_config_map(ns)
            for cm in cms.items:
                k8s_configs["configmaps"].append({
                    "name": cm.metadata.name,
                    "namespace": cm.metadata.namespace,
                    "data_keys": list((cm.data or {}).keys()),
                })
        except ApiException as e:
            self._report_error("ConfigMaps", ns, e)

    def _fetch_secrets(self, ns: str, k8s_configs: dict) -> None:
        try:
            secrets = self.v1.list_namespaced_secret(ns)
            for s in secrets.items:
                k8s_configs["secrets"].append({
                    "name": s.metadata.name,
                    "namespace": s.metadata.namespace,
                    "type": s.type,
                    "data_keys": list((s.data or {}).keys()),
                })
        except ApiException as e:
            self._report_error("Secrets", ns, e)

    def _fetch_hpa(self, ns: str, k8s_configs: dict) -> None:
        try:
            hpas = self.autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(ns)
            for hpa in hpas.items:
                k8s_configs["hpa"].append({
                    "name": hpa.metadata.name,
                    "namespace": hpa.metadata.namespace,
                    "target_ref": {"kind": hpa.spec.scale_target_ref.kind, "name": hpa.spec.scale_target_ref.name},
                    "min_replicas": hpa.spec.min_replicas,
                    "max_replicas": hpa.spec.max_replicas,
                    "current_replicas": hpa.status.current_replicas,
                })
        except ApiException as e:
            self._report_error("HPA", ns, e)

    def _fetch_resource_quotas(self, ns: str, k8s_configs: dict) -> None:
        try:
            quotas = self.v1.list_namespaced_resource_quota(ns)
            for q in quotas.items:
                k8s_configs["resource_quotas"].append({
                    "name": q.metadata.name,
                    "namespace": q.metadata.namespace,
                    "hard": dict(q.spec.hard or {}),
                    "used": dict(q.status.used or {}),
                })
        except ApiException as e:
            self._report_error("ResourceQuotas", ns, e)

    def _fetch_nodes(self, k8s_configs: dict) -> None:
        """Collect node_name -> zone mapping for isolation analysis."""
        try:
            nodes = self.v1.list_node()
            zone_label = "topology.kubernetes.io/zone"
            for n in nodes.items:
                name = n.metadata.name
                labels = n.metadata.labels or {}
                zone = labels.get(zone_label, "")
                # Fallback: some clusters use failure-domain.beta.kubernetes.io/zone
                if not zone:
                    zone = labels.get("failure-domain.beta.kubernetes.io/zone", "")
                k8s_configs["node_zones"][name] = zone
        except ApiException as e:
            self._report_error("Nodes", "*", e)

    def _report_error(self, resource_type: str, ns: str, error) -> None:
        self.context.reporter.report(ReportMessage(
            self.name(),
            ReportType.WARNING,
            f" {resource_type}  failed  ({ns}): {error.reason}"
        ))

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
