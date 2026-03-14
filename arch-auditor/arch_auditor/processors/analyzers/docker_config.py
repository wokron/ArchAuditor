from fastapi.responses import HTMLResponse
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any


class DockerConfigAnalyzer(Processor):

    @staticmethod
    def name() -> str:
        return "DockerConfigAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["DockerConfigSource"]

    def init(self, config) -> bool:
        self.analyzer_config = config or {}
        self.issues: list[dict[str, Any]] = []
        return True

    def process(self) -> None:
        docker_configs = self.context.system_state.extra_attrs.get("docker_configs", [])

        self.issues = []

        for container in docker_configs:
            self._check_container_security(container)

        self.context.system_state.extra_attrs["docker_issues"] = self.issues

    def _check_container_security(self, container: dict) -> None:
        attr = container.get("attrs", {})
        name = attr.get("Name", "unknown")
        resource = f"container/{name}"

        user = attr.get("Config", {}).get("User", "")
        if not user or user == "root" or user == "0":
            self._add_issue(
                "ERROR",
                "RUN_AS_ROOT",
                f"Container '{name}' runs as root user",
                resource,
            )

        privileged = attr.get("HostConfig", {}).get("Privileged", False)
        if privileged:
            self._add_issue(
                "ERROR",
                "PRIVILEGED_MODE",
                f"Container '{name}' runs in privileged mode",
                resource,
            )

        allow_privilege_escalation = attr.get("HostConfig", {}).get("CapAdd", []) or []
        if "CAP_SYS_ADMIN" in allow_privilege_escalation:
            self._add_issue(
                "ERROR",
                "PRIVILEGE_ESCALATION",
                f"Container '{name}' allows privilege escalation (CAP_SYS_ADMIN)",
                resource,
            )

        readonly_rootfs = attr.get("HostConfig", {}).get("ReadonlyRootfs", False)
        if not readonly_rootfs:
            self._add_issue(
                "WARNING",
                "RW_ROOT_FS",
                f"Container '{name}' has writable root filesystem",
                resource,
            )

        cap_add = attr.get("HostConfig", {}).get("CapAdd", []) or []
        cap_drop = attr.get("HostConfig", {}).get("CapDrop", []) or []
        if "ALL" in cap_add:
            self._add_issue(
                "ERROR",
                "ALL_CAPABILITIES",
                f"Container '{name}' adds ALL capabilities",
                resource,
            )
        if len(cap_add) > 0 and "ALL" not in cap_drop:
            self._add_issue(
                "WARNING",
                "CAPABILITIES_NOT_DROPPED",
                f"Container '{name}' does not drop capabilities",
                resource,
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
