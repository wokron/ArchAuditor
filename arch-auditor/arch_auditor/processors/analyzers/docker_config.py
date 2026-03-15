from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any
from collections import defaultdict


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
        self.issues_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.containers_with_issues: dict[str, dict[str, Any]] = {}
        return True

    def process(self) -> None:
        docker_configs = self.context.system_state.extra_attrs.get("docker_configs", [])

        self.issues = []
        self.issues_by_resource = defaultdict(list)
        self.containers_with_issues = {}

        for container in docker_configs:
            self._check_container_security(container)

        self.context.system_state.extra_attrs["docker_issues"] = self.issues

    def _check_container_security(self, container: dict) -> None:
        attr = container.get("attrs", {})
        name = attr.get("Name", "unknown")
        resource = f"container/{name}"
        
        # Store container config for visualization
        if resource not in self.containers_with_issues:
            self.containers_with_issues[resource] = {
                "config": attr,
                "issues": []
            }

        user = attr.get("Config", {}).get("User", "")
        if not user or user == "root" or user == "0":
            self._add_issue(
                "ERROR",
                "RUN_AS_ROOT",
                f"Container '{name}' runs as root user",
                resource,
                "user"
            )

        privileged = attr.get("HostConfig", {}).get("Privileged", False)
        if privileged:
            self._add_issue(
                "ERROR",
                "PRIVILEGED_MODE",
                f"Container '{name}' runs in privileged mode",
                resource,
                "security.privileged"
            )

        allow_privilege_escalation = attr.get("HostConfig", {}).get("CapAdd", []) or []
        if "CAP_SYS_ADMIN" in allow_privilege_escalation:
            self._add_issue(
                "ERROR",
                "PRIVILEGE_ESCALATION",
                f"Container '{name}' allows privilege escalation (CAP_SYS_ADMIN)",
                resource,
                "security.cap_add"
            )

        readonly_rootfs = attr.get("HostConfig", {}).get("ReadonlyRootfs", False)
        if not readonly_rootfs:
            self._add_issue(
                "WARNING",
                "RW_ROOT_FS",
                f"Container '{name}' has writable root filesystem",
                resource,
                "security.read_only_rootfs"
            )

        cap_add = attr.get("HostConfig", {}).get("CapAdd", []) or []
        cap_drop = attr.get("HostConfig", {}).get("CapDrop", []) or []
        if "ALL" in cap_add:
            self._add_issue(
                "ERROR",
                "ALL_CAPABILITIES",
                f"Container '{name}' adds ALL capabilities",
                resource,
                "security.cap_add"
            )
        if len(cap_add) > 0 and "ALL" not in cap_drop:
            self._add_issue(
                "WARNING",
                "CAPABILITIES_NOT_DROPPED",
                f"Container '{name}' does not drop capabilities",
                resource,
                "security.cap_drop"
            )

    def _add_issue(
        self, severity: str, issue_type: str, msg: str, resource: str, config_path: str = ""
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
            "config_path": config_path,
        }
        self.issues.append(issue_data)
        self.issues_by_resource[resource].append(issue_data)
        
        # Add issue to container's issue list for visualization
        if resource in self.containers_with_issues:
            self.containers_with_issues[resource]["issues"].append(issue_data)

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # Get templates directory
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        
        # Prepare container data with configs and issues
        containers = []
        for resource, data in self.containers_with_issues.items():
            container_name = resource.split("/", 1)[1] if "/" in resource else resource
            
            # Import json for serialization
            import json
            
            # Extract user-relevant configuration (like docker-compose.yml)
            full_config = data["config"]
            user_config = self._extract_user_config(full_config)
            config_json = json.dumps(user_config, indent=2, ensure_ascii=False, default=str)
            
            # Determine overall severity
            issues = data["issues"]
            has_error = any(i["severity"] == "ERROR" for i in issues)
            has_warning = any(i["severity"] == "WARNING" for i in issues)
            
            if has_error:
                severity = "error"
            elif has_warning:
                severity = "warning"
            else:
                severity = "passed"
            
            containers.append({
                "name": container_name,
                "resource": resource,
                "severity": severity,
                "config": data["config"],
                "config_json": config_json,
                "issues": issues,
                "issue_count": len(issues),
            })
        
        # Calculate summary
        total_errors = sum(1 for i in self.issues if i["severity"] == "ERROR")
        total_warnings = sum(1 for i in self.issues if i["severity"] == "WARNING")
        
        summary = {
            "total_containers": len(containers),
            "total_errors": total_errors,
            "total_warnings": total_warnings,
        }
        
        return templates.TemplateResponse(
            "docker_config_visualization.html",
            {
                "request": {},
                "title": "Docker 配置安全分析",
                "summary": summary,
                "containers": containers,
            },
        )
    
    def _extract_user_config(self, full_config: dict) -> dict:
        """Extract user-relevant configuration, similar to docker-compose.yml"""
        config = full_config.get("Config", {})
        host_config = full_config.get("HostConfig", {})
        
        user_config = {
            "name": full_config.get("Name", ""),
            "image": config.get("Image", ""),
        }
        
        # User and security context
        user = config.get("User")
        if user:
            user_config["user"] = user
        else:
            # Show explicitly when running as root (security issue)
            user_config["user"] = "root (default)"
        
        # Command and entrypoint
        if config.get("Entrypoint"):
            user_config["entrypoint"] = config.get("Entrypoint")
        if config.get("Cmd"):
            user_config["command"] = config.get("Cmd")
        
        # Working directory
        if config.get("WorkingDir"):
            user_config["working_dir"] = config.get("WorkingDir")
        
        # Environment variables (show count if too many)
        env = config.get("Env", [])
        if env:
            if len(env) > 10:
                user_config["environment"] = f"({len(env)} variables set)"
            else:
                user_config["environment"] = env
        
        # Ports
        exposed_ports = config.get("ExposedPorts", {})
        port_bindings = host_config.get("PortBindings", {})
        if exposed_ports or port_bindings:
            ports = {}
            for port, bindings in port_bindings.items():
                if bindings:
                    ports[port] = bindings[0].get("HostPort", "")
            if ports:
                user_config["ports"] = ports
        
        # Volumes and mounts
        mounts = full_config.get("Mounts", [])
        if mounts:
            user_config["volumes"] = [
                {
                    "source": m.get("Source", ""),
                    "destination": m.get("Destination", ""),
                    "mode": m.get("Mode", ""),
                } for m in mounts
            ]
        
        # Security options - only show relevant ones
        security_config = {}
        
        privileged = host_config.get("Privileged")
        if privileged:
            security_config["privileged"] = True
        
        readonly_rootfs = host_config.get("ReadonlyRootfs")
        if readonly_rootfs is False:
            security_config["read_only_rootfs"] = False
        
        cap_add = host_config.get("CapAdd")
        if cap_add:
            security_config["cap_add"] = cap_add
        else:
            # Show null to indicate capabilities not explicitly added
            security_config["cap_add"] = None
        
        cap_drop = host_config.get("CapDrop")
        if cap_drop:
            security_config["cap_drop"] = cap_drop
        else:
            # Show null to indicate capabilities not dropped
            security_config["cap_drop"] = None
        
        if security_config:
            user_config["security"] = security_config
        
        # Resource limits
        resources = {}
        memory = host_config.get("Memory")
        if memory and memory > 0:
            resources["memory"] = f"{memory // 1024 // 1024}MB"
        
        cpu_shares = host_config.get("CpuShares")
        if cpu_shares and cpu_shares > 0:
            resources["cpu_shares"] = cpu_shares
        
        if resources:
            user_config["resources"] = resources
        
        # Restart policy
        restart_policy = host_config.get("RestartPolicy", {})
        if restart_policy.get("Name"):
            user_config["restart"] = restart_policy.get("Name")
        
        # Network mode
        network_mode = host_config.get("NetworkMode")
        if network_mode and network_mode != "default":
            user_config["network_mode"] = network_mode
        
        return user_config
