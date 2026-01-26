from fastapi.responses import HTMLResponse
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any


class K8sConfigAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "K8sConfigAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource"]

    def init(self, config) -> bool:
        self.analyzer_config = config or {}
        self.issues: list[dict[str, Any]] = []
        self.namespaces = self.analyzer_config.get(
            "namespaces", [self.analyzer_config.get("namespace", "default")]
        )
        return True

    def process(self) -> None:
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})

        self.issues = []

        for dep in k8s_configs.get("deployments", []):
            self._check_security_context("deployment", dep)
            self._check_resource_limits("deployment", dep)
            self._check_probe_settings("deployment", dep)

        for pod in k8s_configs.get("pods", []):
            self._check_security_context("pod", pod)
            self._check_resource_limits("pod", pod)
            self._check_probe_settings("pod", pod)

        self.context.system_state.extra_attrs["k8s_issues"] = self.issues

    def _check_probe_settings(self, config_type, config: dict) -> None:
        if config_type not in ["deployment", "pod"]:
            return

        name = config.get("name", "unknown")
        ns = config.get("namespace", "default")
        if ns not in self.namespaces:
            return

        containers = []
        if config_type == "deployment":
            containers = (
                config.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
        else:
            containers = config.get("spec", {}).get("containers", [])

        for container in containers:
            liveness_probe = container.get("livenessProbe", {})
            readiness_probe = container.get("readinessProbe", {})

            if not liveness_probe:
                self._add_issue(
                    "WARNING",
                    "MISSING_LIVENESS_PROBE",
                    f"Container '{container.get('name', 'unknown')}' in {config_type} '{name}' is missing liveness probe",
                    f"{config_type}/{ns}/{name}",
                )

            if not readiness_probe:
                self._add_issue(
                    "WARNING",
                    "MISSING_READINESS_PROBE",
                    f"Container '{container.get('name', 'unknown')}' in {config_type} '{name}' is missing readiness probe",
                    f"{config_type}/{ns}/{name}",
                )

    def _check_resource_limits(self, config_type, config: dict) -> None:
        if config_type not in ["deployment", "pod"]:
            return

        name = config.get("name", "unknown")
        ns = config.get("namespace", "default")
        if ns not in self.namespaces:
            return

        containers = []
        if config_type == "deployment":
            containers = (
                config.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
        else:
            containers = config.get("spec", {}).get("containers", [])

        for container in containers:
            resources = container.get("resources", {})
            limits = resources.get("limits", {})
            requests = resources.get("requests", {})

            if "cpu" not in limits or "memory" not in limits:
                self._add_issue(
                    "WARNING",
                    "MISSING_RESOURCE_LIMITS",
                    f"Container '{container.get('name', 'unknown')}' in {config_type} '{name}' is missing resource limits",
                    f"{config_type}/{ns}/{name}",
                )

            if "cpu" not in requests or "memory" not in requests:
                self._add_issue(
                    "WARNING",
                    "MISSING_RESOURCE_REQUESTS",
                    f"Container '{container.get('name', 'unknown')}' in {config_type} '{name}' is missing resource requests",
                    f"{config_type}/{ns}/{name}",
                )

    def _check_security_context(self, config_type, config: dict) -> None:
        if config_type not in ["deployment", "pod"]:
            return

        name = config.get("name", "unknown")
        ns = config.get("namespace", "default")
        if ns not in self.namespaces:
            return

        if config_type == "deployment":
            security_context = (
                config.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("security_context", {})
            )
        else:
            security_context = config.get("spec", {}).get("security_context", {})

        if not security_context.get("run_as_non_root", False):
            self._add_issue(
                "ERROR",
                "RUN_AS_ROOT",
                f"{config_type.capitalize()} '{name}' allows running as root",
                f"{config_type}/{ns}/{name}",
            )

        if security_context.get("privileged", False):
            self._add_issue(
                "ERROR",
                "PRIVILEGED_MODE",
                f"{config_type.capitalize()} '{name}' runs in privileged mode",
                f"{config_type}/{ns}/{name}",
            )

        if security_context.get("allow_privilege_escalation", True):
            self._add_issue(
                "ERROR",
                "PRIVILEGE_ESCALATION",
                f"{config_type.capitalize()} '{name}' allows privilege escalation",
                f"{config_type}/{ns}/{name}",
            )

        if not security_context.get("read_only_root_filesystem", False):
            self._add_issue(
                "WARNING",
                "RW_ROOT_FS",
                f"{config_type.capitalize()} '{name}' has writable root filesystem",
                f"{config_type}/{ns}/{name}",
            )

        capabilities = security_context.get("capabilities", {})
        if "ALL" in capabilities.get("add", []):
            self._add_issue(
                "ERROR",
                "ALL_CAPABILITIES",
                f"{config_type.capitalize()} '{name}' adds ALL capabilities",
                f"{config_type}/{ns}/{name}",
            )
        if len(capabilities.get("add", [])) > 0 and "ALL" not in capabilities.get(
            "drop", []
        ):
            self._add_issue(
                "WARNING",
                "CAPABILITIES_NOT_DROPPED",
                f"{config_type.capitalize()} '{name}' does not drop capabilities",
                f"{config_type}/{ns}/{name}",
            )

    def _add_issue(
        self, severity: str, issue_type: str, msg: str, resource: str
    ) -> None:

        self.context.reporter.report(
            ReportMessage(
                report_from=self.name(),
                report_type=(
                    ReportType.ERROR if severity == "ERROR" else ReportType.WARNING
                ),
                message=f"{resource}: [{issue_type}] {msg}",
            )
        )

        self.issues.append(
            {
                "severity": severity,
                "type": issue_type,
                "message": msg,
                "resource": resource,
            }
        )

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # TODO: Implement actual visualization logic
        items = []
        for issue in self.issues:
            items.append(
                f"{issue['severity']}: [{issue['type']}] {issue['message']} (Resource: {issue['resource']})"
            )
        html = "<html><body><ul>"
        for item in items:
            html += f"<li>{item}</li>"
        html += "</ul></body></html>"
        return HTMLResponse(content=html)
