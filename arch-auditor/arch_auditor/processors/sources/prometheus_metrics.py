from ...processors import Processor
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import JSONResponse
import time


class PrometheusMetricsSource(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.prometheus_url: str | None = None
        self.metrics_timeseries: dict = {
            "latency": {},
            "error_rate": {},
            "throughput": {},
        }

    @staticmethod
    def name() -> str:
        return "PrometheusMetricsSource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource"]

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type

        if source_type == "Prometheus":
            prometheus_url = config.get("prometheus_url", None)
            if prometheus_url is None:
                return False
            self.prometheus_url = prometheus_url
            self.range_seconds = config.get("range_seconds", 3600)
            self.step = config.get("step", "60s")

            self.latency_query = config.get(
                "latency_query",
                """
                histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le, service_name))
                or histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket[5m])) by (le, service_name)) / 1000
                or histogram_quantile(0.95, sum(rate(http_server_duration_seconds_bucket[5m])) by (le, service_name))
                or histogram_quantile(0.95, sum(rate(rpc_server_duration_milliseconds_bucket[5m])) by (le, service_name)) / 1000
                or histogram_quantile(0.95, sum(rate(traces_span_metrics_duration_milliseconds_bucket[5m])) by (le, service_name)) / 1000
            """,
            )
            self.throughput_query = config.get(
                "throughput_query",
                """
                sum(rate(http_server_request_duration_seconds_count[5m])) by (service_name)
                or sum(rate(http_server_duration_milliseconds_count[5m])) by (service_name)
                or sum(rate(http_server_duration_seconds_count[5m])) by (service_name)
                or sum(rate(rpc_server_duration_milliseconds_count[5m])) by (service_name)
                or sum(rate(traces_span_metrics_calls_total[5m])) by (service_name)
            """,
            )
            self.error_rate_query = config.get(
                "error_rate_query",
                """
                (
                  sum(rate(http_server_duration_milliseconds_count{http_status_code=~"5.."}[5m])) by (service_name)
                  or sum(rate(rpc_server_duration_milliseconds_count{rpc_grpc_status_code!="0"}[5m])) by (service_name)
                  or sum(rate(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[5m])) by (service_name)
                )
                /
                (
                  sum(rate(http_server_duration_milliseconds_count[5m])) by (service_name)
                  or sum(rate(rpc_server_duration_milliseconds_count[5m])) by (service_name)
                  or sum(rate(traces_span_metrics_calls_total[5m])) by (service_name)
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
            latency_data = self._query_prometheus_range(self.latency_query)
            self._update_timeseries(latency_data, "latency")

            error_rate_data = self._query_prometheus_range(self.error_rate_query)
            self._update_timeseries(error_rate_data, "error_rate")

            throughput_data = self._query_prometheus_range(self.throughput_query)
            self._update_timeseries(throughput_data, "throughput")

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
            service_name = (
                metric_labels.get("service_name")
                or metric_labels.get("service")
                or metric_labels.get("job")
                or metric_labels.get("app")
            )
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
                self.metrics_timeseries[metric_name][service_name] = timeseries

    def _update_graph_metrics(self) -> None:
        G = self.context.system_state.graph
        for metric_name, services_data in self.metrics_timeseries.items():
            for service_name, timeseries in services_data.items():
                if service_name in G.nodes and timeseries:
                    G.nodes[service_name][metric_name] = timeseries

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
            }
        return JSONResponse(
            content=services_timeseries,
        )
