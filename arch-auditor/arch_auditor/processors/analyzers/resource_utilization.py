import statistics
from pathlib import Path

import networkx as nx
from fastapi.templating import Jinja2Templates

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class ResourceUtilizationAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "ResourceUtilizationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "PrometheusMetricsSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.waste_threshold = float(config.get("waste_threshold", 0.3))
        self.limit_risk_threshold = float(config.get("limit_risk_threshold", 0.7))
        self.high_qps_multiplier = float(config.get("high_qps_multiplier", 3.0))
        self.sync_entry_services = list(config.get("sync_entry_services", []) or [])
        self.sync_entry_patterns = [
            pattern.lower()
            for pattern in (
                config.get(
                    "sync_entry_patterns",
                    ["frontend", "gateway", "proxy", "web", "api"],
                )
                or []
            )
        ]
        return True

    def process(self) -> None:
        graph = getattr(self.context.system_state, "graph", None)
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        deployments = k8s_configs.get("deployments", [])
        metrics_timeseries = self.context.system_state.extra_attrs.get(
            "metrics_timeseries", {}
        )
        cpu_usage_data = metrics_timeseries.get("cpu_usage", {})
        memory_usage_data = metrics_timeseries.get("memory_usage", {})
        throughput_data = metrics_timeseries.get("throughput", {})

        avg_throughput_by_service = {
            service: self._summarize_timeseries(points)[0]
            for service, points in (throughput_data or {}).items()
        }
        throughput_values = [
            value
            for value in avg_throughput_by_service.values()
            if value is not None and value > 0
        ]
        cluster_median_throughput = (
            statistics.median(throughput_values) if throughput_values else None
        )

        sync_services, sync_entry_services = self._identify_sync_services(graph)
        summaries = []

        for dep in deployments:
            name = dep.get("name", "unknown")
            actual_throughput_timeseries = throughput_data.get(name, [])
            avg_throughput, _short_term_avg_throughput = self._summarize_timeseries(
                actual_throughput_timeseries
            )
            is_sync_path_service = name in sync_services
            is_high_qps_service = self._is_high_qps(
                avg_throughput, cluster_median_throughput
            )
            requires_request = is_sync_path_service or is_high_qps_service

            missing_request_reason = None
            if is_sync_path_service:
                missing_request_reason = "synchronous_call_path"
            elif is_high_qps_service:
                missing_request_reason = "high_qps_async_service"

            for container in dep.get("containers", []):
                container_name = container.get("name", "unknown")
                requests = container.get("resources", {}).get("requests", {})
                limits = container.get("resources", {}).get("limits", {})

                request_cpu = self._parse_k8s_cpu(requests.get("cpu"))
                limit_cpu = self._parse_k8s_cpu(limits.get("cpu"))
                request_memory = self._parse_k8s_memory_bytes(requests.get("memory"))
                limit_memory = self._parse_k8s_memory_bytes(limits.get("memory"))

                actual_cpu_timeseries = cpu_usage_data.get(name) or cpu_usage_data.get(
                    container_name, []
                )
                actual_memory_timeseries = memory_usage_data.get(name) or (
                    memory_usage_data.get(container_name, [])
                )

                avg_cpu_usage, short_term_avg_cpu_usage = self._summarize_timeseries(
                    actual_cpu_timeseries
                )
                avg_memory_usage, short_term_avg_memory_usage = (
                    self._summarize_timeseries(actual_memory_timeseries)
                )

                has_missing_request_issue = False
                missing_cpu_request = "cpu" not in requests or request_cpu <= 0
                missing_memory_request = (
                    "memory" not in requests or request_memory <= 0
                )
                if requires_request:
                    if missing_cpu_request:
                        has_missing_request_issue = True
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=(
                                    ReportType.ERROR
                                    if is_sync_path_service
                                    else ReportType.WARNING
                                ),
                                message=self._missing_request_message(
                                    name=name,
                                    resource_kind="CPU",
                                    avg_throughput=avg_throughput,
                                    cluster_median_throughput=cluster_median_throughput,
                                    is_sync_path_service=is_sync_path_service,
                                ),
                            )
                        )
                    if missing_memory_request:
                        has_missing_request_issue = True
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=(
                                    ReportType.ERROR
                                    if is_sync_path_service
                                    else ReportType.WARNING
                                ),
                                message=self._missing_request_message(
                                    name=name,
                                    resource_kind="memory",
                                    avg_throughput=avg_throughput,
                                    cluster_median_throughput=cluster_median_throughput,
                                    is_sync_path_service=is_sync_path_service,
                                ),
                            )
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
                        "request_cpu": request_cpu,
                        "limit_cpu": limit_cpu,
                        "request_memory_bytes": request_memory,
                        "limit_memory_bytes": limit_memory,
                        "avg_cpu_usage": avg_cpu_usage,
                        "short_term_avg_cpu_usage": short_term_avg_cpu_usage,
                        "avg_memory_usage_bytes": avg_memory_usage,
                        "short_term_avg_memory_usage_bytes": short_term_avg_memory_usage,
                        "avg_throughput": avg_throughput,
                        "cluster_median_throughput": cluster_median_throughput,
                        "high_qps_multiplier": self.high_qps_multiplier,
                        "cpu_timeseries_points": len(actual_cpu_timeseries or []),
                        "memory_timeseries_points": len(actual_memory_timeseries or []),
                        "throughput_timeseries_points": len(
                            actual_throughput_timeseries or []
                        ),
                        "is_sync_path_service": is_sync_path_service,
                        "is_async_only_service": not is_sync_path_service,
                        "is_high_qps_service": is_high_qps_service,
                        "requires_request": requires_request,
                        "missing_request_reason": missing_request_reason,
                        "sync_entry_services": list(sync_entry_services),
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

    def _identify_sync_services(self, graph) -> tuple[set[str], list[str]]:
        if graph is None or graph.number_of_nodes() == 0:
            return set(), []

        explicit_entries = [
            service
            for service in self.sync_entry_services
            if service in graph.nodes and service != "<SOURCE>"
        ]
        if explicit_entries:
            seed_services = explicit_entries
        else:
            seed_services = [
                node
                for node in graph.nodes
                if node != "<SOURCE>" and self._matches_sync_entry_pattern(node)
            ]
            if not seed_services:
                seed_services = [
                    node
                    for node in graph.nodes
                    if node != "<SOURCE>" and graph.in_degree(node) == 0
                ]

        sync_services = set()
        for service in seed_services:
            sync_services.add(service)
            sync_services.update(nx.descendants(graph, service))
        sync_services.discard("<SOURCE>")
        return sync_services, sorted(set(seed_services))

    def _matches_sync_entry_pattern(self, service_name: str) -> bool:
        normalized = str(service_name).lower()
        return any(pattern in normalized for pattern in self.sync_entry_patterns)

    def _is_high_qps(
        self, avg_throughput: float | None, cluster_median_throughput: float | None
    ) -> bool:
        if (
            avg_throughput is None
            or cluster_median_throughput is None
            or cluster_median_throughput <= 0
        ):
            return False
        return avg_throughput > self.high_qps_multiplier * cluster_median_throughput

    def _missing_request_message(
        self,
        name: str,
        resource_kind: str,
        avg_throughput: float | None,
        cluster_median_throughput: float | None,
        is_sync_path_service: bool,
    ) -> str:
        if is_sync_path_service:
            return (
                f"Service '{name}' is on a user-facing synchronous call path "
                f"but has no {resource_kind} request configured."
            )
        avg = "unknown" if avg_throughput is None else f"{avg_throughput:.3f}"
        median = (
            "unknown"
            if cluster_median_throughput is None
            else f"{cluster_median_throughput:.3f}"
        )
        return (
            f"Async service '{name}' has very high throughput "
            f"(avg: {avg}, cluster median: {median}) "
            f"but has no {resource_kind} request configured."
        )

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
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        summaries = (
            self.context.system_state.extra_attrs.get(
                "resource_utilization_summary", []
            )
            or []
        )

        def clamp_width(value: float | None) -> float:
            if value is None:
                return 0.0
            return max(0.0, min(100.0, value))

        def ratio_percent(numerator: float | None, denominator: float | None) -> float | None:
            if numerator is None or denominator is None or denominator <= 0:
                return None
            return numerator / denominator * 100

        def format_percent(value: float | None) -> str:
            if value is None:
                return "-"
            return f"{value:.1f}%"

        def format_cpu(value: float | None) -> str:
            if value is None:
                return "-"
            if value <= 0:
                return "未配置"
            if value < 1:
                return f"{value * 1000:.0f}m"
            return f"{value:.2f} cores"

        def format_bytes(value: float | None) -> str:
            if value is None:
                return "-"
            if value <= 0:
                return "未配置"
            units = ["B", "KiB", "MiB", "GiB", "TiB"]
            current = float(value)
            index = 0
            while current >= 1024 and index < len(units) - 1:
                current /= 1024
                index += 1
            return f"{current:.1f} {units[index]}"

        def issue_labels(item: dict) -> list[str]:
            labels = []
            if item.get("has_missing_request_issue"):
                labels.append("缺少 request")
            if item.get("has_cpu_limit_risk_issue"):
                labels.append("CPU limit 风险")
            if item.get("has_memory_limit_risk_issue"):
                labels.append("内存 limit 风险")
            if item.get("has_cpu_waste_issue"):
                labels.append("CPU 浪费")
            if item.get("has_memory_waste_issue"):
                labels.append("内存浪费")
            if item.get("is_sync_path_service"):
                labels.append("同步链路")
            elif item.get("is_high_qps_service"):
                labels.append("高 QPS")
            return labels

        def severity(item: dict) -> str:
            if item.get("has_missing_request_issue") or item.get("has_limit_risk_issue"):
                return "error"
            if item.get("has_waste_issue"):
                return "warning"
            if item.get("requires_request"):
                return "info"
            return "normal"

        def severity_score(item: dict) -> int:
            score = 0
            if item.get("has_missing_request_issue"):
                score += 100
            if item.get("has_limit_risk_issue"):
                score += 80
            if item.get("has_waste_issue"):
                score += 30
            if item.get("requires_request"):
                score += 5
            return score

        cards = []
        for item in summaries:
            request_cpu = item.get("request_cpu")
            limit_cpu = item.get("limit_cpu")
            avg_cpu = item.get("avg_cpu_usage")
            short_cpu = item.get("short_term_avg_cpu_usage")

            request_memory = item.get("request_memory_bytes")
            limit_memory = item.get("limit_memory_bytes")
            avg_memory = item.get("avg_memory_usage_bytes")
            short_memory = item.get("short_term_avg_memory_usage_bytes")

            cpu_request_percent = ratio_percent(avg_cpu, request_cpu)
            cpu_limit_percent = ratio_percent(short_cpu, limit_cpu)
            memory_request_percent = ratio_percent(avg_memory, request_memory)
            memory_limit_percent = ratio_percent(short_memory, limit_memory)

            cards.append(
                {
                    "service": item.get("service") or "-",
                    "container": item.get("container") or "-",
                    "severity": severity(item),
                    "score": severity_score(item),
                    "labels": issue_labels(item),
                    "avg_throughput": item.get("avg_throughput"),
                    "cpu": {
                        "request": format_cpu(request_cpu),
                        "limit": format_cpu(limit_cpu),
                        "avg": format_cpu(avg_cpu),
                        "short": format_cpu(short_cpu),
                        "request_percent": format_percent(cpu_request_percent),
                        "limit_percent": format_percent(cpu_limit_percent),
                        "request_width": clamp_width(cpu_request_percent),
                        "limit_width": clamp_width(cpu_limit_percent),
                    },
                    "memory": {
                        "request": format_bytes(request_memory),
                        "limit": format_bytes(limit_memory),
                        "avg": format_bytes(avg_memory),
                        "short": format_bytes(short_memory),
                        "request_percent": format_percent(memory_request_percent),
                        "limit_percent": format_percent(memory_limit_percent),
                        "request_width": clamp_width(memory_request_percent),
                        "limit_width": clamp_width(memory_limit_percent),
                    },
                }
            )

        cards.sort(
            key=lambda card: (
                -card["score"],
                card["service"],
                card["container"],
            )
        )

        stats = {
            "total": len(summaries),
            "risky": sum(
                1
                for item in summaries
                if item.get("has_missing_request_issue")
                or item.get("has_waste_issue")
                or item.get("has_limit_risk_issue")
            ),
            "missing_request": sum(
                1 for item in summaries if item.get("has_missing_request_issue")
            ),
            "limit_risk": sum(1 for item in summaries if item.get("has_limit_risk_issue")),
            "waste": sum(1 for item in summaries if item.get("has_waste_issue")),
        }

        return templates.TemplateResponse(
            "resource_utilization_visualization.html",
            {
                "request": {},
                "stats": stats,
                "cards": cards,
                "waste_threshold": self.waste_threshold,
                "limit_risk_threshold": self.limit_risk_threshold,
            },
        )
