from fastapi.responses import HTMLResponse
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any


class K8sConfigAnalyzer(Processor):
    #分析 K8s 配置   
    @staticmethod
    def name() -> str:
        return "K8sConfigAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource"]

    def init(self, config) -> bool:
        self.analyzer_config = config or {}
        self.min_replicas = self.analyzer_config.get("min_replicas", 2)
        self.max_restarts = self.analyzer_config.get("max_restarts", 5)
        self.issues: list[dict[str, Any]] = []
        return True

    def process(self) -> None:
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        # TODO: Implement actual analysis logic
        
        self.issues = []
        
        for dep in k8s_configs.get("deployments", []):
            self._check_deployment(dep)
        
        for pod in k8s_configs.get("pods", []):
            self._check_pod(pod)
        
        for hpa in k8s_configs.get("hpa", []):
            self._check_hpa(hpa)
        
        self.context.system_state.extra_attrs["k8s_issues"] = self.issues
        
        errors = sum(1 for i in self.issues if i["severity"] == "ERROR")
        warnings = sum(1 for i in self.issues if i["severity"] == "WARNING")
        
        if errors > 0 or warnings > 0:
            self.context.reporter.report(ReportMessage(
                self.name(), ReportType.WARNING,
                f" {errors} errors, {warnings} warnings"
            ))
        else:
            self.context.reporter.report(ReportMessage(
                self.name(), ReportType.INFO, "no issues "
            ))

    def _check_deployment(self, dep: dict) -> None:
        name = dep.get("name", "unknown")
        ns = dep.get("namespace", "default")
        
        # 检查副本数
        replicas = dep.get("replicas", 0)
        if replicas < self.min_replicas:
            self._add_issue("WARNING", "LOW_REPLICAS",
                f"Deployment '{name}'  {replicas}",
                f"deployment/{ns}/{name}")
        
        # 检查容器
        for c in dep.get("containers", []):
            cname = c.get("name", "unknown")
            image = c.get("image", "")
            
            if ":latest" in image or ":" not in image.split("/")[-1]:
                self._add_issue("WARNING", "LATEST_TAG",
                    f" '{cname}' use latest",
                    f"deployment/{ns}/{name}")
            
            resources = c.get("resources", {})
            if not resources.get("limits"):
                self._add_issue("ERROR", "NO_LIMITS",
                    f"container '{cname}' no  limits",
                    f"deployment/{ns}/{name}")

    def _check_pod(self, pod: dict) -> None:
        name = pod.get("name", "unknown")
        ns = pod.get("namespace", "default")
        
        restarts = pod.get("restart_count", 0)
        if restarts > self.max_restarts:
            self._add_issue("ERROR", "HIGH_RESTARTS",
                f"Pod '{name}' high restarts: {restarts}",
                f"pod/{ns}/{name}")
        
        phase = pod.get("phase", "")
        if phase not in ["Running", "Succeeded"]:
            self._add_issue("WARNING", "BAD_STATUS",
                f"Pod '{name}' bad status: {phase}",
                f"pod/{ns}/{name}")

    def _check_hpa(self, hpa: dict) -> None:
        name = hpa.get("name", "unknown")
        ns = hpa.get("namespace", "default")
        
        min_replicas = hpa.get("min_replicas", 0)
        if min_replicas < 2:
            self._add_issue("WARNING", "HPA_LOW_MIN",
                f"HPA '{name}' 的 minReplicas 过低: {min_replicas}",
                f"hpa/{ns}/{name}")
        
        current = hpa.get("current_replicas", 0)
        max_replicas = hpa.get("max_replicas", 0)
        if current >= max_replicas and max_replicas > 0:
            self._add_issue("WARNING", "HPA_AT_MAX",
                f"HPA '{name}' reach max replicas",
                f"hpa/{ns}/{name}")

    def _add_issue(self, severity: str, issue_type: str, msg: str, resource: str) -> None:
        self.issues.append({
            "severity": severity,
            "type": issue_type,
            "message": msg,
            "resource": resource,
        })

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # TODO: Implement actual visualization logic
        items = ["item1", "item2", "item3"]
        html = "<html><body><ul>"
        for item in items:
            html += f"<li>{item}</li>"
        html += "</ul></body></html>"
        return HTMLResponse(content=html)
