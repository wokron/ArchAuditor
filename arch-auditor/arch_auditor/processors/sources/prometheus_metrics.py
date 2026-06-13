from ...processors import Processor
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import JSONResponse
import time
import copy
from collections import defaultdict


class PrometheusMetricsSource(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.prometheus_url: str | None = None
        self.metrics_timeseries: dict = self._empty_metrics_store()
        self.metric_aliases: dict = {}
        self.raw_metrics_timeseries: dict = self._empty_metrics_store()

    @staticmethod
    def name() -> str:
        return "PrometheusMetricsSource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    @staticmethod
    def _empty_metrics_store() -> dict:
        return {
            "latency": {},
            "error_rate": {},
            "throughput": {},
            "cpu_usage": {},
            "memory_usage": {},
        }

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type
        self.latency_query = ""
        self.error_rate_query = ""
        self.throughput_query = ""
        self.cpu_usage_query = ""
        self.memory_usage_query = ""

        if source_type == "Mock":
            metrics = config.get("metrics", {})
            for metric_name, services_data in metrics.items():
                slot = self.metrics_timeseries.setdefault(metric_name, {})
                for service_name, timeseries in services_data.items():
                    slot[service_name] = timeseries
            self._update_graph_metrics()
            return True
        elif source_type == "Prometheus":
            prometheus_url = config.get("prometheus_url", None)
            if prometheus_url is None:
                return False
            self.prometheus_url = prometheus_url
            self.range_seconds = config.get("range_seconds", 3600)
            self.step = config.get("step", "60s")

            self.latency_query = config.get(
                "latency_query",
                """
                (
                  sum by (service_name) (traces_span_metrics_duration_milliseconds_sum)
                  /
                  clamp_min(sum by (service_name) (traces_span_metrics_duration_milliseconds_count), 1)
                )
                or
                (
                  sum by (service_name) (rate(http_server_duration_milliseconds_sum[5m]))
                  /
                  clamp_min(sum by (service_name) (rate(http_server_duration_milliseconds_count[5m])), 1)
                )
                or
                (
                  sum by (service_name) (rate(http_server_request_duration_seconds_sum[5m]))
                  /
                  clamp_min(sum by (service_name) (rate(http_server_request_duration_seconds_count[5m])), 1)
                )
            """,
            )
            self.throughput_query = config.get(
                "throughput_query",
                """
                sum by (service_name) (traces_span_metrics_calls_total)
                or sum by (service_name) (http_server_duration_milliseconds_count)
                or sum by (service_name) (http_server_request_duration_seconds_count)
            """,
            )
            self.error_rate_query = config.get(
                "error_rate_query",
                """
                (
                  sum by (service_name) (traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"})
                  or sum by (service_name) (http_server_duration_milliseconds_count{http_status_code=~"5.."})
                  or sum by (service_name) (http_server_request_duration_seconds_count{http_response_status_code=~"5.."})
                )
                /
                clamp_min(
                  (
                    sum by (service_name) (traces_span_metrics_calls_total)
                    or sum by (service_name) (http_server_duration_milliseconds_count)
                    or sum by (service_name) (http_server_request_duration_seconds_count)
                  ),
                  1
                )
            """,
            )
            self.cpu_usage_query = config.get(
                "cpu_usage_query",
                """
                (
                  sum by (container_name) (rate(container_cpu_usage_nanoseconds_total[5m]))
                  / 1e9
                )
                or
                (
                  sum by (pod) (
                    rate(container_cpu_usage_seconds_total{pod!=""}[5m])
                  )
                )
                or
                (
                  sum by (service_name) (
                    rate(process_cpu_seconds_total[5m])
                  )
                )
            """,
            )
            self.memory_usage_query = config.get(
                "memory_usage_query",
                """
                (
                  sum by (container_name) (
                    container_memory_usage_total_bytes{
                      container_name!="",
                      container_name!="POD",
                      container_name!~"^k8s_.*"
                    }
                  )
                )
                or
                (
                  sum by (container_name) (
                    container_memory_usage_bytes{
                      container_name!="",
                      container_name!="POD",
                      container_name!~"^k8s_.*"
                    }
                  )
                )
                or
                (
                  sum by (container) (
                    container_memory_usage_total_bytes{
                      container!="",
                      container!="POD",
                      container!~"^k8s_.*"
                    }
                  )
                )
                or
                (
                  sum by (container) (
                    container_memory_usage_bytes{
                      container!="",
                      container!="POD",
                      container!~"^k8s_.*"
                    }
                  )
                )
                or
                (
                  sum by (pod) (
                    container_memory_usage_total_bytes{
                      pod!="",
                      pod!="POD"
                    }
                  )
                )
                or
                (
                  sum by (pod) (
                    container_memory_usage_bytes{
                      pod!="",
                      pod!="POD"
                    }
                  )
                )
                or
                (
                  sum by (service_name) (process_memory_usage_bytes)
                )
                or
                (
                  sum by (service_name) (process_resident_memory_bytes)
                )
                or
                (
                  sum by (service_name) (otelcol_process_memory_rss_bytes)
                )
            """,
            )
            return True
        return False

    def process(self) -> None:
        if self.source_type == "Prometheus":
            self._process_prometheus()

    def _process_prometheus(self) -> None:
        try:
            self.metrics_timeseries = self._empty_metrics_store()
            self.raw_metrics_timeseries = self._empty_metrics_store()
            self.metric_aliases = {}

            latency_data = self._query_prometheus_range(self.latency_query)
            self._update_timeseries(latency_data, "latency")

            error_rate_data = self._query_prometheus_range(self.error_rate_query)
            self._update_timeseries(error_rate_data, "error_rate")

            throughput_data = self._query_prometheus_range(self.throughput_query)
            self._update_timeseries(throughput_data, "throughput")

            cpu_usage_data = self._query_prometheus_range(self.cpu_usage_query)
            self._update_timeseries(cpu_usage_data, "cpu_usage")

            memory_usage_data = self._query_prometheus_range(self.memory_usage_query)
            self._update_timeseries(memory_usage_data, "memory_usage")

            self.raw_metrics_timeseries = copy.deepcopy(self.metrics_timeseries)
            self._update_graph_metrics()
        except requests.exceptions.RequestException as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Failed to fetch Prometheus metrics: {e}",
                )
            )

    def _query_prometheus_range(self, query: str) -> dict:
        url = f"{self.prometheus_url}/api/v1/query_range"
        end_time = time.time()
        start_time = end_time - self.range_seconds
        params = {
            "query": query,
            "start": start_time,
            "end": end_time,
            "step": self.step,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def _update_timeseries(self, prometheus_response: dict, metric_name: str) -> None:
        if prometheus_response.get("status") != "success":
            return

        results = prometheus_response.get("data", {}).get("result", [])
        for result in results:
            metric_labels = result.get("metric", {})
            service_name = self._extract_metric_series_name(metric_labels)
            if service_name:

                def process_value(val):
                    if val in ("NaN", "+Inf", "-Inf", "inf", "-inf"):
                        return None
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return None

                timeseries = list(
                    map(lambda x: (x[0], process_value(x[1])), result.get("values", []))
                )
                self.metrics_timeseries.setdefault(metric_name, {})[service_name] = timeseries

    @staticmethod
    def _extract_metric_series_name(metric_labels: dict) -> str | None:
        for label_name in (
            "service_name",
            "service",
            "container_name",
            "container",
            "pod",
            "job",
            "app",
        ):
            raw_value = metric_labels.get(label_name)
            canonical_value = PrometheusMetricsSource._canonical_metric_series_name(
                raw_value, label_name
            )
            if canonical_value:
                return canonical_value
        return None

    @staticmethod
    def _canonical_metric_series_name(
        raw_value: str | None, label_name: str
    ) -> str | None:
        if raw_value is None:
            return None

        value = str(raw_value).strip()
        if not value:
            return None

        if label_name in ("container_name", "container") and value.startswith("k8s_"):
            parts = value.split("_")
            if len(parts) >= 3 and parts[1]:
                return parts[1]

        return value

    def _update_graph_metrics(self) -> None:
        G = self.context.system_state.graph
        graph_name_index = self._build_graph_name_index(G)
        self.metric_aliases = {}
        aggregated_metrics = defaultdict(dict)
        for metric_name, services_data in self.metrics_timeseries.items():
            for service_name, timeseries in services_data.items():
                matched_name = self._match_graph_service_name(service_name, graph_name_index)
                if matched_name and timeseries:
                    self.metric_aliases.setdefault(service_name, matched_name)
                    existing = aggregated_metrics[metric_name].get(matched_name, [])
                    aggregated_metrics[metric_name][matched_name] = self._merge_timeseries(
                        existing, timeseries
                    )
                elif timeseries:
                    aggregated_metrics[metric_name][service_name] = self._merge_timeseries(
                        aggregated_metrics[metric_name].get(service_name, []),
                        timeseries,
                    )
        for metric_name, services_data in aggregated_metrics.items():
            for service_name, timeseries in services_data.items():
                if service_name in G.nodes:
                    G.nodes[service_name][metric_name] = timeseries
        # Write to extra_attrs so downstream analyzers (e.g. ResourceUtilizationAnalyzer)
        # can consume metrics_timeseries without accessing graph nodes directly.
        normalized_metrics = {}
        for metric_name, services_data in aggregated_metrics.items():
            normalized_metrics[metric_name] = dict(services_data)
        self.context.system_state.extra_attrs["metrics_timeseries"] = normalized_metrics
        self.context.system_state.extra_attrs["raw_metrics_timeseries"] = copy.deepcopy(
            self.raw_metrics_timeseries
        )
        self.context.system_state.extra_attrs["metrics_aliases"] = dict(self.metric_aliases)
        self.context.system_state.extra_attrs["prometheus_queries"] = {
            "latency": self.latency_query.strip(),
            "error_rate": self.error_rate_query.strip(),
            "throughput": self.throughput_query.strip(),
            "cpu_usage": self.cpu_usage_query.strip(),
            "memory_usage": self.memory_usage_query.strip(),
        }

    @staticmethod
    def _normalize_service_name(name: str) -> str:
        return "".join(ch for ch in str(name).lower() if ch.isalnum())

    def _build_graph_name_index(self, graph) -> dict:
        index = {}
        for node in graph.nodes:
            normalized = self._normalize_service_name(node)
            index.setdefault(normalized, []).append(node)
        return index

    def _match_graph_service_name(self, metric_service_name: str, graph_name_index: dict):
        normalized_metric_name = self._normalize_service_name(metric_service_name)
        direct_matches = graph_name_index.get(normalized_metric_name, [])
        if direct_matches:
            return direct_matches[0]

        candidates = []
        for normalized_graph_name, graph_nodes in graph_name_index.items():
            shorter_len = min(
                len(normalized_metric_name), len(normalized_graph_name)
            )
            if shorter_len < 3:
                continue
            if (
                normalized_metric_name.startswith(normalized_graph_name)
                or normalized_graph_name.startswith(normalized_metric_name)
                or normalized_metric_name.endswith(normalized_graph_name)
                or normalized_graph_name.endswith(normalized_metric_name)
            ):
                for graph_node in graph_nodes:
                    candidates.append((len(normalized_graph_name), graph_node))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_length = candidates[0][0]
        best_nodes = []
        for candidate_length, candidate_node in candidates:
            if candidate_length != best_length:
                break
            if candidate_node not in best_nodes:
                best_nodes.append(candidate_node)
        if len(best_nodes) == 1:
            return best_nodes[0]
        return None

    @staticmethod
    def _merge_timeseries(existing: list, incoming: list) -> list:
        if not existing:
            return list(incoming)
        if not incoming:
            return list(existing)

        merged = {}
        for timestamp, value in existing:
            merged[timestamp] = value
        for timestamp, value in incoming:
            previous = merged.get(timestamp)
            if previous is None:
                merged[timestamp] = value
            elif value is None:
                merged[timestamp] = previous
            else:
                merged[timestamp] = previous + value
        return sorted(merged.items(), key=lambda item: item[0])

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        G = self.context.system_state.graph
        services_timeseries = {}
        for node in G.nodes:
            node_data = G.nodes[node]
            services_timeseries[node] = {
                "latency": node_data.get("latency"),
                "error_rate": node_data.get("error_rate"),
                "throughput": node_data.get("throughput"),
                "cpu_usage": node_data.get("cpu_usage"),
                "memory_usage": node_data.get("memory_usage"),
            }
        return JSONResponse(
            content=services_timeseries,
        )
