import statistics

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class MonolithicServiceAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "MonolithicServiceAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource", "K8sConfigSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.degree_threshold = config.get("degree_threshold", 15)
        self.resource_multiplier = config.get("resource_multiplier", 5.0)
        self.min_timeseries_points = config.get("min_timeseries_points", 3)
        return True

    def process(self) -> None:
        self._service_summaries: dict[str, dict] = {}
        self._analyze_architecture_perspective()
        self._analyze_resource_perspective()
        self.context.system_state.extra_attrs["monolithic_service_summary"] = sorted(
            self._service_summaries.values(), key=lambda item: item["service"]
        )

    def _analyze_architecture_perspective(self):
        if not hasattr(self.context.system_state, "graph"):
            return
        G = self.context.system_state.graph
        for node in G.nodes:
            in_degree = G.in_degree(node)
            out_degree = G.out_degree(node)
            degree = in_degree + out_degree
            summary = self._ensure_service_summary(node)
            summary["in_degree"] = in_degree
            summary["out_degree"] = out_degree
            summary["degree"] = degree
            summary["degree_threshold"] = self.degree_threshold
            if degree > self.degree_threshold:
                summary["has_architecture_issue"] = True
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Architecture perspective: Service '{node}' has a high degree ({degree}). "
                            f"In-degree: {in_degree}, Out-degree: {out_degree}. "
                            f"This exceeds the threshold of {self.degree_threshold} and indicates a potential monolithic service."
                        ),
                    )
                )

    def _analyze_resource_perspective(self):
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        deployments = k8s_configs.get("deployments", [])
        metrics_timeseries = self.context.system_state.extra_attrs.get(
            "metrics_timeseries", {}
        )
        cpu_usage_data = metrics_timeseries.get("cpu_usage", {})
        memory_usage_data = metrics_timeseries.get("memory_usage", {})

        for dep in deployments:
            service_name = dep.get("name", "unknown")
            containers = dep.get("containers", []) or []
            summary = self._ensure_service_summary(service_name)
            actual_cpu_timeseries = self._get_service_metric_timeseries(
                service_name, containers, cpu_usage_data
            )
            actual_memory_timeseries = self._get_service_metric_timeseries(
                service_name, containers, memory_usage_data
            )

            summary["avg_cpu_usage"] = self._average_from_timeseries(
                actual_cpu_timeseries
            )
            summary["avg_memory_usage_bytes"] = self._average_from_timeseries(
                actual_memory_timeseries
            )
            summary["cpu_timeseries_points"] = len(actual_cpu_timeseries or [])
            summary["memory_timeseries_points"] = len(actual_memory_timeseries or [])

            total_request_cpu = 0.0
            total_limit_cpu = 0.0
            total_request_memory = 0.0
            total_limit_memory = 0.0
            for container in containers:
                resources = container.get("resources", {})
                total_request_cpu += self._parse_k8s_cpu(
                    resources.get("requests", {}).get("cpu")
                )
                total_limit_cpu += self._parse_k8s_cpu(
                    resources.get("limits", {}).get("cpu")
                )
                total_request_memory += self._parse_k8s_memory_bytes(
                    resources.get("requests", {}).get("memory")
                )
                total_limit_memory += self._parse_k8s_memory_bytes(
                    resources.get("limits", {}).get("memory")
                )

            summary["total_request_cpu"] = total_request_cpu
            summary["total_limit_cpu"] = total_limit_cpu
            summary["total_request_memory_bytes"] = total_request_memory
            summary["total_limit_memory_bytes"] = total_limit_memory

        self._flag_resource_outliers(
            metric_key="avg_cpu_usage",
            median_key="cluster_median_avg_cpu_usage",
            issue_key="has_resource_cpu_monolith_issue",
            label="CPU utilization",
        )
        self._flag_resource_outliers(
            metric_key="avg_memory_usage_bytes",
            median_key="cluster_median_avg_memory_usage_bytes",
            issue_key="has_resource_memory_monolith_issue",
            label="memory usage",
            format_value=lambda value: f"{value:.0f} bytes",
        )

    def _flag_resource_outliers(
        self,
        metric_key: str,
        median_key: str,
        issue_key: str,
        label: str,
        format_value=None,
    ) -> None:
        comparable_summaries = [
            summary
            for summary in self._service_summaries.values()
            if summary.get(metric_key) is not None
            and summary.get(
                "cpu_timeseries_points"
                if metric_key == "avg_cpu_usage"
                else "memory_timeseries_points",
                0,
            )
            >= self.min_timeseries_points
        ]
        if not comparable_summaries:
            return

        median_value = statistics.median(
            summary[metric_key] for summary in comparable_summaries
        )
        for summary in self._service_summaries.values():
            summary[median_key] = median_value
            summary.setdefault("has_resource_monolith_issue", False)
            summary.setdefault(issue_key, False)

        if median_value <= 0:
            return

        if format_value is None:
            format_value = lambda value: f"{value:.3f}"

        for summary in comparable_summaries:
            observed_value = summary[metric_key]
            if observed_value <= self.resource_multiplier * median_value:
                continue
            summary[issue_key] = True
            summary["has_resource_monolith_issue"] = True
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        f"Resource perspective: Service '{summary['service']}' has average actual "
                        f"{label} {format_value(observed_value)}, which is > "
                        f"{self.resource_multiplier}x the median service {label} "
                        f"({format_value(median_value)}) over the observed time window."
                    ),
                )
            )

    def _get_service_metric_timeseries(
        self, service_name: str, containers: list[dict], metric_data: dict
    ) -> list:
        direct_timeseries = metric_data.get(service_name)
        if direct_timeseries:
            return direct_timeseries

        merged = {}
        for container in containers:
            container_name = container.get("name")
            if not container_name:
                continue
            for timestamp, value in metric_data.get(container_name, []) or []:
                previous = merged.get(timestamp)
                if previous is None:
                    merged[timestamp] = value
                elif value is None:
                    merged[timestamp] = previous
                elif previous is None:
                    merged[timestamp] = value
                else:
                    merged[timestamp] = previous + value
        return sorted(merged.items(), key=lambda item: item[0])

    @staticmethod
    def _average_from_timeseries(timeseries: list) -> float | None:
        values = [value for _, value in (timeseries or []) if value is not None]
        return statistics.mean(values) if values else None

    def _ensure_service_summary(self, service_name: str) -> dict:
        existing = self._service_summaries.get(service_name)
        if existing is not None:
            return existing

        summary = {
            "service": service_name,
            "in_degree": None,
            "out_degree": None,
            "degree": None,
            "degree_threshold": self.degree_threshold,
            "has_architecture_issue": False,
            "avg_cpu_usage": None,
            "avg_memory_usage_bytes": None,
            "cpu_timeseries_points": 0,
            "memory_timeseries_points": 0,
            "cluster_median_avg_cpu_usage": None,
            "cluster_median_avg_memory_usage_bytes": None,
            "total_request_cpu": 0.0,
            "total_limit_cpu": 0.0,
            "total_request_memory_bytes": 0.0,
            "total_limit_memory_bytes": 0.0,
            "has_resource_cpu_monolith_issue": False,
            "has_resource_memory_monolith_issue": False,
            "has_resource_monolith_issue": False,
        }
        self._service_summaries[service_name] = summary
        return summary

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
