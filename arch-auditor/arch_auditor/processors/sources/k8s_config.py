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
        self.namespaces = self.k8s_config.get(
            "namespaces", 
            [self.k8s_config.get("namespace", "default")]
        )
        
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
        k8s_configs = {
            "deployments": [],
            "services": [],
            "pods": [],
            "configmaps": [],
            "secrets": [],
            "hpa": [],
            "resource_quotas": [],
        }
        
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
                containers = []
                for c in (dep.spec.template.spec.containers or []):
                    info = {"name": c.name, "image": c.image}
                    if c.resources:
                        info["resources"] = {
                            "limits": dict(c.resources.limits or {}),
                            "requests": dict(c.resources.requests or {}),
                        }
                    containers.append(info)
                
                k8s_configs["deployments"].append({
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "replicas": dep.spec.replicas,
                    "ready_replicas": dep.status.ready_replicas or 0,
                    "containers": containers,
                    "labels": dict(dep.metadata.labels or {}),
                })
        except ApiException as e:
            self._report_error("Deployments", ns, e)

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
            for pod in pods.items:
                restart_count = sum(
                    cs.restart_count for cs in (pod.status.container_statuses or []) 
                    if cs.restart_count
                )
                k8s_configs["pods"].append({
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node_name": pod.spec.node_name,
                    "restart_count": restart_count,
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
