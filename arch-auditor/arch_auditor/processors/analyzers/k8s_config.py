import html as html_lib
from fastapi import Request
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
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {}) or {}

        # Reset each run so the visualization reflects the latest scan only.
        self.issues = []

        for dep in k8s_configs.get("deployments", []) or []:
            self._check_security_context("deployment", dep)
            self._check_resource_limits("deployment", dep)
            self._check_probe_settings("deployment", dep)

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


    def visualize(self, request: Request):
        issues = self.issues or []
        total = len(issues)
        query_params = request.query_params if request else {}

        def normalize_list(value: str | None) -> list[str]:
            if not value:
                return []
            return [item.strip() for item in value.split(",") if item.strip()]

        def get_list(primary: str, fallback: str) -> list[str]:
            items = []
            if request:
                items = query_params.getlist(primary)
            if not items:
                items = normalize_list(query_params.get(primary))
            if not items and fallback:
                if request:
                    items = query_params.getlist(fallback)
                if not items:
                    items = normalize_list(query_params.get(fallback))
            return items

        namespace_filter = get_list("namespace", "ns")
        severity_filter = [item.upper() for item in get_list("severity", "sev")]

        auto_enabled = query_params.get("auto") == "1"
        try:
            interval = int(query_params.get("interval") or "10")
        except ValueError:
            interval = 10
        if interval not in [5, 10, 20, 30, 60]:
            interval = 10

        def match_namespace(resource: str) -> bool:
            if not namespace_filter:
                return True
            parts = str(resource).split("/")
            ns = parts[1] if len(parts) > 1 else ""
            return ns in namespace_filter

        def match_severity(severity: str) -> bool:
            if not severity_filter:
                return True
            return severity in severity_filter

        filtered = []
        for issue in issues:
            severity = str(issue.get("severity", "WARNING")).upper()
            resource = issue.get("resource", "")
            if match_namespace(resource) and match_severity(severity):
                filtered.append(issue)

        counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
        for issue in filtered:
            severity = str(issue.get("severity", "WARNING")).upper()
            if severity not in counts:
                severity = "WARNING"
            counts[severity] += 1

        def resource_kind(resource: str) -> str:
            parts = str(resource).split("/")
            return parts[0] if parts else "resource"

        kind_counts: dict[str, int] = {}
        for issue in filtered:
            kind = resource_kind(issue.get("resource", "resource"))
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        sorted_kinds = sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))
        max_kind = max(kind_counts.values(), default=0)

        def escape(value: Any) -> str:
            return html_lib.escape(str(value))

        def severity_class(severity: str) -> str:
            if severity == "ERROR":
                return "sev sev-error"
            if severity == "WARNING":
                return "sev sev-warning"
            return "sev sev-info"

        rows = []
        for issue in filtered:
            severity = str(issue.get("severity", "WARNING")).upper()
            issue_type = issue.get("type", "-")
            message = issue.get("message", "-")
            resource = issue.get("resource", "-")
            parts = str(resource).split("/")
            res_kind = parts[0] if len(parts) > 0 else "-"
            namespace = parts[1] if len(parts) > 1 else "-"
            name = parts[2] if len(parts) > 2 else resource
            rows.append(
                f"""
                <tr>
                  <td><span class=\"{severity_class(severity)}\">{escape(severity)}</span></td>
                  <td>{escape(issue_type)}</td>
                  <td>{escape(res_kind)}</td>
                  <td>{escape(namespace)}</td>
                  <td>{escape(name)}</td>
                  <td class=\"message\">{escape(message)}</td>
                </tr>
                """
            )

        empty_state = "<div class=\"empty\">No issues detected for the selected filters.</div>"

        unique_namespaces = sorted(
            {
                str(resource).split("/")[1]
                for resource in [issue.get("resource", "") for issue in issues]
                if len(str(resource).split("/")) > 1
            }
            or set(self.namespaces)
        )
        selected_namespace = namespace_filter[0] if namespace_filter else "all"
        selected_severities = set(severity_filter)
        severity_options = ["ERROR", "WARNING", "INFO"]

        from urllib.parse import urlencode

        def build_query(
            namespace_value: str,
            severities: list[str],
            auto: bool,
            interval_value: int,
        ) -> str:
            params = []
            if namespace_value and namespace_value != "all":
                params.append(("namespace", namespace_value))
            for sev in severities:
                params.append(("severity", sev))
            if auto:
                params.append(("auto", "1"))
                params.append(("interval", str(interval_value)))
            return "?" + urlencode(params, doseq=True) if params else ""

        auto_toggle_url = build_query(
            selected_namespace, list(selected_severities), not auto_enabled, interval
        )
        clear_url = build_query("all", [], auto_enabled, interval)
        current_url = build_query(
            selected_namespace, list(selected_severities), auto_enabled, interval
        )
        auto_label = "Disable Auto" if auto_enabled else "Enable Auto"
        auto_hidden = (
            '<input type="hidden" name="auto" value="1">' if auto_enabled else ""
        )
        interval_options = "".join(
            [
                f"<option value='{sec}'{' selected' if sec == interval else ''}>{sec}s</option>"
                for sec in [5, 10, 20, 30, 60]
            ]
        )
        namespace_options = "".join(
            [
                f"<option value='{escape(ns)}'{' selected' if ns == selected_namespace else ''}>{escape(ns)}</option>"
                for ns in unique_namespaces
            ]
        )
        severity_checks = "".join(
            [
                f"<label><input type='checkbox' name='severity' value='{sev}'{' checked' if sev in selected_severities else ''}> {sev}</label>"
                for sev in severity_options
            ]
        )
        meta_refresh = (
            f"<meta http-equiv=\"refresh\" content=\"{interval}\">"
            if auto_enabled
            else ""
        )

        filtered_total = len(filtered)
        table_html = ""
        if filtered_total:
            table_html = f"""
              <table>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Issue Type</th>
                    <th>Kind</th>
                    <th>Namespace</th>
                    <th>Name</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(rows)}
                </tbody>
              </table>
            """

        chart_html = "".join(
            [
                f"<div class='bar'><div class='bar-name'>{escape(kind)}</div><div class='bar-track'><div class='bar-fill' style='width: {int((count / max_kind) * 100) if max_kind else 0}%'></div></div><div class='bar-count'>{count}</div></div>"
                for kind, count in sorted_kinds
            ]
        )
        if not chart_html:
            chart_html = "<div class=\"muted\">No resources available.</div>"

        html = f"""
        <!DOCTYPE html>
        <html lang=\"en\">
          <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            {meta_refresh}
            <title>K8s Config Analyzer</title>
            <style>
              :root {{
                --bg: #f8fafc;
                --card: #ffffff;
                --border: #e2e8f0;
                --text: #0f172a;
                --muted: #64748b;
                --error: #ef4444;
                --warning: #f59e0b;
                --info: #3b82f6;
              }}
              * {{ box-sizing: border-box; }}
              body {{
                margin: 0;
                font-family: \"Segoe UI\", Arial, sans-serif;
                background: var(--bg);
                color: var(--text);
                padding: 24px;
              }}
              .container {{
                max-width: 1200px;
                margin: 0 auto;
                display: flex;
                flex-direction: column;
                gap: 16px;
              }}
              .header {{
                display: flex;
                flex-direction: column;
                gap: 6px;
              }}
              .header h1 {{
                margin: 0;
                font-size: 24px;
              }}
              .muted {{
                color: var(--muted);
                font-size: 14px;
              }}
              .summary {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
              }}
              .filters {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 16px;
              }}
              .filters label {{
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: var(--muted);
                margin-bottom: 6px;
                display: block;
              }}
              .filters select, .filters button, .filters a.btn {{
                width: 100%;
                padding: 8px 10px;
                border-radius: 8px;
                border: 1px solid var(--border);
                font-size: 14px;
                background: #fff;
                text-decoration: none;
                color: var(--text);
                display: inline-flex;
                align-items: center;
                justify-content: center;
              }}
              .filters .actions {{
                display: flex;
                gap: 8px;
                align-items: center;
              }}
              .filters .actions button.primary {{
                background: var(--info);
                color: #fff;
                border-color: var(--info);
              }}
              .filters .severity {{
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
              }}
              .filters .severity label {{
                display: flex;
                align-items: center;
                gap: 6px;
                font-size: 13px;
                text-transform: none;
                letter-spacing: 0;
                color: var(--text);
                margin: 0;
              }}
              .filters .auto-refresh {{
                display: flex;
                flex-direction: column;
                gap: 8px;
              }}
              .card {{
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 16px;
              }}
              .card h3 {{
                margin: 0 0 8px 0;
                font-size: 14px;
                color: var(--muted);
              }}
              .card .value {{
                font-size: 24px;
                font-weight: 700;
              }}
              .chart {{
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 16px;
              }}
              .bar {{
                display: flex;
                align-items: center;
                gap: 12px;
                margin-bottom: 10px;
              }}
              .bar:last-child {{
                margin-bottom: 0;
              }}
              .bar-name {{
                width: 120px;
                font-size: 13px;
                color: var(--muted);
              }}
              .bar-track {{
                flex: 1;
                height: 10px;
                border-radius: 999px;
                background: #e2e8f0;
                overflow: hidden;
              }}
              .bar-fill {{
                height: 10px;
                border-radius: 999px;
                background: var(--info);
              }}
              .bar-count {{
                width: 40px;
                text-align: right;
                font-size: 13px;
              }}
              table {{
                width: 100%;
                border-collapse: collapse;
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                overflow: hidden;
              }}
              th, td {{
                padding: 12px 14px;
                border-bottom: 1px solid var(--border);
                text-align: left;
                font-size: 14px;
                vertical-align: top;
              }}
              th {{
                background: #f1f5f9;
                color: var(--muted);
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.05em;
              }}
              tr:last-child td {{
                border-bottom: none;
              }}
              .sev {{
                display: inline-block;
                padding: 2px 8px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 700;
              }}
              .sev-error {{
                background: rgba(239, 68, 68, 0.12);
                color: var(--error);
                border: 1px solid rgba(239, 68, 68, 0.25);
              }}
              .sev-warning {{
                background: rgba(245, 158, 11, 0.15);
                color: var(--warning);
                border: 1px solid rgba(245, 158, 11, 0.3);
              }}
              .sev-info {{
                background: rgba(59, 130, 246, 0.12);
                color: var(--info);
                border: 1px solid rgba(59, 130, 246, 0.25);
              }}
              .message {{
                color: var(--muted);
              }}
              .empty {{
                padding: 24px;
                background: var(--card);
                border: 1px dashed var(--border);
                border-radius: 12px;
                color: var(--muted);
                text-align: center;
              }}
            </style>
          </head>
          <body>
            <div class=\"container\">
              <div class=\"header\">
                <h1>K8s Config Analyzer</h1>
                <div class=\"muted\">Namespaces: {escape(", ".join(self.namespaces))}</div>
              </div>
              <form class=\"filters\" method=\"get\">
                <div>
                  <label for=\"namespaceFilter\">Namespace</label>
                  <select id=\"namespaceFilter\" name=\"namespace\">
                    <option value=\"all\"{' selected' if selected_namespace == 'all' else ''}>All</option>
                    {namespace_options}
                  </select>
                </div>
                <div>
                  <label>Severity</label>
                  <div class=\"severity\">
                    {severity_checks}
                  </div>
                </div>
                <div class=\"actions\">
                  <button class=\"primary\" type=\"submit\">Apply</button>
                  <a class=\"btn\" href=\"{clear_url or '?'}\">Clear</a>
                </div>
                <div class=\"auto-refresh\">
                  <label for=\"refreshInterval\">Auto Refresh</label>
                  <select id=\"refreshInterval\" name=\"interval\">
                    {interval_options}
                  </select>
                  {auto_hidden}
                  <a class=\"btn\" href=\"{auto_toggle_url or '?'}\">{auto_label}</a>
                  <a class=\"btn\" href=\"{current_url or '?'}\">Refresh Now</a>
                </div>
              </form>
              <div class=\"summary\">
                <div class=\"card\">
                  <h3>Total Issues</h3>
                  <div class=\"value\">{filtered_total}</div>
                  <div class=\"muted\">{total} total</div>
                </div>
                <div class=\"card\">
                  <h3>Errors</h3>
                  <div class=\"value\" style=\"color: var(--error);\">{counts["ERROR"]}</div>
                </div>
                <div class=\"card\">
                  <h3>Warnings</h3>
                  <div class=\"value\" style=\"color: var(--warning);\">{counts["WARNING"]}</div>
                </div>
                <div class=\"card\">
                  <h3>Info</h3>
                  <div class=\"value\" style=\"color: var(--info);\">{counts["INFO"]}</div>
                </div>
              </div>
              <div class=\"chart\">
                <div class=\"muted\" style=\"margin-bottom: 8px;\">Resource Kind Distribution</div>
                {chart_html}
              </div>
              {empty_state if filtered_total == 0 else ""}
              {table_html}
            </div>
          </body>
        </html>
        """
        return HTMLResponse(content=html)

