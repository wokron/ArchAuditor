import statistics

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class ResourceUtilizationAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "ResourceUtilizationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "ServicePrioritySource", "PrometheusMetricsSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.waste_threshold = config.get("waste_threshold", 0.3)
        self.limit_risk_threshold = config.get("limit_risk_threshold", 0.7)
        return True

    def process(self) -> None:
        summaries = []
        graph = None
        if hasattr(self.context.system_state, "graph"):
            graph = self.context.system_state.graph

        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        deployments = k8s_configs.get("deployments", [])
        metrics_timeseries = self.context.system_state.extra_attrs.get(
            "metrics_timeseries", {}
        )
        cpu_usage_data = metrics_timeseries.get("cpu_usage", {})
        memory_usage_data = metrics_timeseries.get("memory_usage", {})

        for dep in deployments:
            name = dep.get("name", "unknown")
            priority = -1
            if graph and name in graph.nodes:
                priority = graph.nodes[name].get("priority", -1)

            for container in dep.get("containers", []):
                container_name = container.get("name", "unknown")
                requests = container.get("resources", {}).get("requests", {})
                limits = container.get("resources", {}).get("limits", {})

                request_cpu = self._parse_k8s_cpu(requests.get("cpu"))
                limit_cpu = self._parse_k8s_cpu(limits.get("cpu"))
                request_memory = self._parse_k8s_memory_bytes(requests.get("memory"))
                limit_memory = self._parse_k8s_memory_bytes(limits.get("memory"))

                has_missing_request_issue = False
                if priority != -1 and priority <= 1:
                    if "cpu" not in requests or request_cpu <= 0:
                        has_missing_request_issue = True
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.ERROR,
                                message=(
                                    f"Service '{name}' is a high-priority service "
                                    f"(priority {priority}) but has no CPU request "
                                    "configured. This is unreasonable."
                                ),
                            )
                        )
                    if "memory" not in requests or request_memory <= 0:
                        has_missing_request_issue = True
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.ERROR,
                                message=(
                                    f"Service '{name}' is a high-priority service "
                                    f"(priority {priority}) but has no memory request "
                                    "configured. This is unreasonable."
                                ),
                            )
                        )

                actual_cpu_timeseries = cpu_usage_data.get(name) or cpu_usage_data.get(
                    container_name, []
                )
                actual_memory_timeseries = memory_usage_data.get(
                    name
                ) or memory_usage_data.get(container_name, [])

                avg_cpu_usage, short_term_avg_cpu_usage = self._summarize_timeseries(
                    actual_cpu_timeseries
                )
                avg_memory_usage, short_term_avg_memory_usage = (
                    self._summarize_timeseries(actual_memory_timeseries)
                )

                has_cpu_waste_issue = False
                if (
                    avg_cpu_usage is not None
                    and request_cpu > 0
                    and avg_cpu_usage < self.waste_threshold * request_cpu
                ):
                    has_cpu_waste_issue = True
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.WARNING,
                            message=(
                                f"Service '{name}' (container '{container_name}') is wasting CPU. "
                                f"Actual average utilization ({avg_cpu_usage:.3f}) < "
                                f"{self.waste_threshold * 100}% of request ({request_cpu})."
                            ),
                        )
                    )

                has_memory_waste_issue = False
                if (
                    avg_memory_usage is not None
                    and request_memory > 0
                    and avg_memory_usage < self.waste_threshold * request_memory
                ):
                    has_memory_waste_issue = True
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.WARNING,
                            message=(
                                f"Service '{name}' (container '{container_name}') is wasting memory. "
                                f"Actual average utilization ({avg_memory_usage:.0f} bytes) < "
                                f"{self.waste_threshold * 100}% of request ({request_memory:.0f} bytes)."
                            ),
                        )
                    )

                has_cpu_limit_risk_issue = False
                if (
                    short_term_avg_cpu_usage is not None
                    and limit_cpu > 0
                    and short_term_avg_cpu_usage
                    > self.limit_risk_threshold * limit_cpu
                ):
                    has_cpu_limit_risk_issue = True
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.WARNING,
                            message=(
                                f"Service '{name}' (container '{container_name}') CPU stability risk. "
                                f"Short-term average utilization ({short_term_avg_cpu_usage:.3f}) > "
                                f"{self.limit_risk_threshold * 100}% of limit ({limit_cpu})."
                            ),
                        )
                    )

                has_memory_limit_risk_issue = False
                if (
                    short_term_avg_memory_usage is not None
                    and limit_memory > 0
                    and short_term_avg_memory_usage
                    > self.limit_risk_threshold * limit_memory
                ):
                    has_memory_limit_risk_issue = True
                    self.context.reporter.report(
                        ReportMessage(
                            report_from=self.name(),
                            report_type=ReportType.WARNING,
                            message=(
                                f"Service '{name}' (container '{container_name}') memory stability risk. "
                                f"Short-term average utilization ({short_term_avg_memory_usage:.0f} bytes) > "
                                f"{self.limit_risk_threshold * 100}% of limit ({limit_memory:.0f} bytes)."
                            ),
                        )
                    )

                summaries.append(
                    {
                        "service": name,
                        "container": container_name,
                        "priority": priority,
                        "request_cpu": request_cpu,
                        "limit_cpu": limit_cpu,
                        "request_memory_bytes": request_memory,
                        "limit_memory_bytes": limit_memory,
                        "avg_cpu_usage": avg_cpu_usage,
                        "short_term_avg_cpu_usage": short_term_avg_cpu_usage,
                        "avg_memory_usage_bytes": avg_memory_usage,
                        "short_term_avg_memory_usage_bytes": short_term_avg_memory_usage,
                        "cpu_timeseries_points": len(actual_cpu_timeseries or []),
                        "memory_timeseries_points": len(actual_memory_timeseries or []),
                        "has_missing_request_issue": has_missing_request_issue,
                        "has_cpu_waste_issue": has_cpu_waste_issue,
                        "has_memory_waste_issue": has_memory_waste_issue,
                        "has_waste_issue": has_cpu_waste_issue or has_memory_waste_issue,
                        "has_cpu_limit_risk_issue": has_cpu_limit_risk_issue,
                        "has_memory_limit_risk_issue": has_memory_limit_risk_issue,
                        "has_limit_risk_issue": (
                            has_cpu_limit_risk_issue or has_memory_limit_risk_issue
                        ),
                    }
                )

        self.context.system_state.extra_attrs["resource_utilization_summary"] = summaries

    @staticmethod
    def _summarize_timeseries(timeseries: list) -> tuple[float | None, float | None]:
        values = [value for _, value in (timeseries or []) if value is not None]
        avg_utilization = statistics.mean(values) if values else None
        short_term_values = values[-5:] if len(values) >= 5 else values
        short_term_avg_utilization = (
            statistics.mean(short_term_values) if short_term_values else None
        )
        return avg_utilization, short_term_avg_utilization

    def _parse_k8s_cpu(self, cpu_str):
        if not cpu_str:
            return 0.0
        cpu_str = str(cpu_str)
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1000.0
        try:
            return float(cpu_str)
        except ValueError:
            return 0.0

    def _parse_k8s_memory_bytes(self, mem_str):
        if not mem_str:
            return 0.0
        mem_str = str(mem_str)
        units = {
            "Ki": 1024,
            "Mi": 1024**2,
            "Gi": 1024**3,
            "Ti": 1024**4,
            "K": 1000,
            "M": 1000**2,
            "G": 1000**3,
            "T": 1000**4,
        }
        for suffix, multiplier in units.items():
            if mem_str.endswith(suffix):
                try:
                    return float(mem_str[: -len(suffix)]) * multiplier
                except ValueError:
                    return 0.0
        try:
            return float(mem_str)
        except ValueError:
            return 0.0

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
