import html as html_lib
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any
from collections import defaultdict


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
        self.issues_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.namespaces = self.analyzer_config.get(
            "namespaces", [self.analyzer_config.get("namespace", "default")]
        )
        return True

    def process(self) -> None:
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {}) or {}

        # Reset each run so the visualization reflects the latest scan only.
        self.issues = []
        self.issues_by_resource = defaultdict(list)

        for dep in k8s_configs.get("deployments", []) or []:
            self._check_security_context("deployment", dep)
            self._check_resource_limits("deployment", dep)
            self._check_probe_settings("deployment", dep)
            self._check_host_path_mounts("deployment", dep)

        for pod in k8s_configs.get("pods", []) or []:
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

        containers = config.get("containers")
        if containers is None:
            if config_type == "deployment":
                containers = (
                    config.get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                )
            else:
                containers = config.get("spec", {}).get("containers", [])

        if not containers:
            return

        # If the source didn't provide probe information at all, don't generate
        # noisy "missing probe" warnings.
        probe_keys = {
            "livenessProbe",
            "readinessProbe",
            "liveness_probe",
            "readiness_probe",
        }
        if not any(
            isinstance(container, dict) and probe_keys.intersection(container.keys())
            for container in containers
        ):
            return

        for container in containers:
            if not isinstance(container, dict):
                continue

            liveness_probe = (
                container.get("livenessProbe") or container.get("liveness_probe") or {}
            )
            readiness_probe = (
                container.get("readinessProbe")
                or container.get("readiness_probe")
                or {}
            )

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

        containers = config.get("containers")
        if containers is None:
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
            if not isinstance(container, dict):
                continue

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
                config.get("securityContext")
                or config.get("security_context")
                or config.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("securityContext")
                or config.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("security_context")
            )
        else:
            security_context = (
                config.get("securityContext")
                or config.get("security_context")
                or config.get("spec", {}).get("securityContext")
                or config.get("spec", {}).get("security_context")
            )

        # If the source doesn't provide security context data, don't emit false
        # positives.
        if not isinstance(security_context, dict) or not security_context:
            return

        run_as_non_root = security_context.get("run_as_non_root")
        if run_as_non_root is None:
            run_as_non_root = security_context.get("runAsNonRoot")
        if run_as_non_root is False:
            self._add_issue(
                "ERROR",
                "RUN_AS_ROOT",
                f"{config_type.capitalize()} '{name}' allows running as root",
                f"{config_type}/{ns}/{name}",
            )

        if security_context.get("privileged") is True:
            self._add_issue(
                "ERROR",
                "PRIVILEGED_MODE",
                f"{config_type.capitalize()} '{name}' runs in privileged mode",
                f"{config_type}/{ns}/{name}",
            )

        allow_privilege_escalation = security_context.get("allow_privilege_escalation")
        if allow_privilege_escalation is None:
            allow_privilege_escalation = security_context.get("allowPrivilegeEscalation")
        if allow_privilege_escalation is True:
            self._add_issue(
                "ERROR",
                "PRIVILEGE_ESCALATION",
                f"{config_type.capitalize()} '{name}' allows privilege escalation",
                f"{config_type}/{ns}/{name}",
            )

        read_only_root_filesystem = security_context.get("read_only_root_filesystem")
        if read_only_root_filesystem is None:
            read_only_root_filesystem = security_context.get("readOnlyRootFilesystem")
        if read_only_root_filesystem is False:
            self._add_issue(
                "WARNING",
                "RW_ROOT_FS",
                f"{config_type.capitalize()} '{name}' has writable root filesystem",
                f"{config_type}/{ns}/{name}",
            )

        capabilities = security_context.get("capabilities") or {}
        if not isinstance(capabilities, dict):
            return
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

    def _check_host_path_mounts(self, config_type, config: dict) -> None:
        """Warn if the workload mounts local host paths (hostPath volumes)."""
        if config_type != "deployment":
            return

        name = config.get("name", "unknown")
        ns = config.get("namespace", "default")
        if ns not in self.namespaces:
            return

        volumes = config.get("volumes")
        if not volumes:
            return

        for vol in volumes:
            if not isinstance(vol, dict):
                continue
            host_path = vol.get("hostPath") or vol.get("host_path")
            if host_path:
                path = host_path.get("path", "") if isinstance(host_path, dict) else str(host_path)
                self._add_issue(
                    "WARNING",
                    "HOST_PATH_MOUNT",
                    f"Deployment '{name}' mounts host path '{path}' via volume '{vol.get('name', 'unknown')}'. "
                    f"This binds the pod to a specific node and may cause security risks.",
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

        issue_data = {
            "severity": severity,
            "type": issue_type,
            "message": msg,
            "resource": resource,
        }
        self.issues.append(issue_data)
        self.issues_by_resource[resource].append(issue_data)

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # Get templates directory
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        
        # Build resource list with issues
        resources = []
        all_resources = set()
        
        # Add all deployments and pods as resources
        for dep in k8s_configs.get("deployments", []):
            name = dep.get("name", "unknown")
            ns = dep.get("namespace", "default")
            if ns in self.namespaces:
                resource_name = f"deployment/{ns}/{name}"
                all_resources.add(resource_name)
        
        for pod in k8s_configs.get("pods", []):
            name = pod.get("name", "unknown")
            ns = pod.get("namespace", "default")
            if ns in self.namespaces:
                resource_name = f"pod/{ns}/{name}"
                all_resources.add(resource_name)
        
        for resource_name in sorted(all_resources):
            issues = self.issues_by_resource.get(resource_name, [])
            
            # Determine severity
            has_error = any(i["severity"] == "ERROR" for i in issues)
            has_warning = any(i["severity"] == "WARNING" for i in issues)
            
            if has_error:
                severity = "error"
            elif has_warning:
                severity = "warning"
            else:
                severity = "passed"
            
            resources.append({
                "name": resource_name,
                "severity": severity,
                "issue_count": len(issues),
                "issues": issues,
            })
        
        # Calculate summary
        total_errors = sum(1 for i in self.issues if i["severity"] == "ERROR")
        total_warnings = sum(1 for i in self.issues if i["severity"] == "WARNING")
        total_passed = len([r for r in resources if r["severity"] == "passed"])
        
        summary = {
            "total_resources": len(resources),
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "total_passed": total_passed,
        }
        
        return templates.TemplateResponse(
            "config_issues_visualization.html",
            {
                "request": {},
                "title": "Kubernetes 配置安全分析",
                "summary": summary,
                "resources": resources,
            },
        )
