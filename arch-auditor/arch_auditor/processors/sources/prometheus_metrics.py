from ...processors import Processor
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import JSONResponse
import time
import copy
from collections import defaultdict


class PrometheusMetricsSource(Processor):
    DEFAULT_RANGE_SECONDS = 3600
    DEFAULT_STEP = "60s"
    DEFAULT_TIMEOUT_SECONDS = 10
    DEFAULT_REQUEST_MAX_RETRIES = 1
    DEFAULT_REQUEST_RETRY_BACKOFF_SECONDS = 1.0
    DEFAULT_MEMORY_RANGE_SECONDS = 1800
    DEFAULT_MEMORY_STEP = "300s"
    DEFAULT_MEMORY_TIMEOUT_SECONDS = 20
    DEFAULT_MEMORY_FALLBACK_RANGE_SECONDS = 86400
    DEFAULT_MEMORY_FALLBACK_STEP = "1800s"
    DEFAULT_SERVICE_METRIC_FALLBACK_RANGE_SECONDS = 600
    DEFAULT_SERVICE_METRIC_FALLBACK_STEP = "120s"

    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.prometheus_url: str | None = None
        self.metrics_timeseries: dict = self._empty_metrics_store()
        self.metric_aliases: dict = {}
        self.raw_metrics_timeseries: dict = self._empty_metrics_store()
        self.cached_metrics_timeseries: dict = self._empty_metrics_store()
        self.request_max_retries = self.DEFAULT_REQUEST_MAX_RETRIES
        self.request_retry_backoff_seconds = (
            self.DEFAULT_REQUEST_RETRY_BACKOFF_SECONDS
        )
        self.service_metric_fallback_range_seconds = (
            self.DEFAULT_SERVICE_METRIC_FALLBACK_RANGE_SECONDS
        )
        self.service_metric_fallback_step = self.DEFAULT_SERVICE_METRIC_FALLBACK_STEP
        self.memory_fallback_range_seconds = self.DEFAULT_MEMORY_FALLBACK_RANGE_SECONDS
        self.memory_fallback_step = self.DEFAULT_MEMORY_FALLBACK_STEP
        self.latency_query = ""
        self.latency_query_variants = self._default_latency_query_variants()
        self.error_rate_query = ""
        self.error_rate_query_variants = self._default_error_rate_query_variants()
        self.throughput_query = ""
        self.throughput_query_variants = self._default_throughput_query_variants()

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

    @staticmethod
    def _default_latency_query_variants() -> list[dict]:
        return [
            {
                "label": "trace_span_metrics",
                "query": """
                    sum by (service_name) (traces_span_metrics_duration_milliseconds_sum)
                    /
                    clamp_min(sum by (service_name) (traces_span_metrics_duration_milliseconds_count), 1)
                """,
            },
            {
                "label": "http_server_duration_milliseconds",
                "query": """
                    sum by (service_name) (rate(http_server_duration_milliseconds_sum[5m]))
                    /
                    clamp_min(sum by (service_name) (rate(http_server_duration_milliseconds_count[5m])), 1)
                """,
            },
            {
                "label": "http_server_request_duration_seconds",
                "query": """
                    sum by (service_name) (rate(http_server_request_duration_seconds_sum[5m]))
                    /
                    clamp_min(sum by (service_name) (rate(http_server_request_duration_seconds_count[5m])), 1)
                """,
            },
        ]

    @staticmethod
    def _default_error_rate_query_variants() -> list[dict]:
        return [
            {
                "label": "trace_span_metrics",
                "query": """
                    sum by (service_name) (traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"})
                    /
                    clamp_min(sum by (service_name) (traces_span_metrics_calls_total), 1)
                """,
            },
            {
                "label": "http_server_duration_milliseconds",
                "query": """
                    sum by (service_name) (http_server_duration_milliseconds_count{http_status_code=~"5.."})
                    /
                    clamp_min(sum by (service_name) (http_server_duration_milliseconds_count), 1)
                """,
            },
            {
                "label": "http_server_request_duration_seconds",
                "query": """
                    sum by (service_name) (http_server_request_duration_seconds_count{http_response_status_code=~"5.."})
                    /
                    clamp_min(sum by (service_name) (http_server_request_duration_seconds_count), 1)
                """,
            },
        ]

    @staticmethod
    def _default_throughput_query_variants() -> list[dict]:
        return [
            {
                "label": "trace_span_metrics",
                "query": """
                    sum by (service_name) (traces_span_metrics_calls_total)
                """,
            },
            {
                "label": "http_server_duration_milliseconds",
                "query": """
                    sum by (service_name) (http_server_duration_milliseconds_count)
                """,
            },
            {
                "label": "http_server_request_duration_seconds",
                "query": """
                    sum by (service_name) (http_server_request_duration_seconds_count)
                """,
            },
        ]

    @staticmethod
    def _compose_or_query(query_variants: list[dict]) -> str:
        parts = []
        for variant in query_variants or []:
            query = str(variant.get("query", "")).strip()
            if query:
                parts.append(f"(\n{query}\n)")
        return "\nor\n".join(parts)

    @staticmethod
    def _normalize_query_variants(configured_variants) -> list[dict]:
        normalized = []
        for index, item in enumerate(configured_variants or []):
            if isinstance(item, str):
                query = item.strip()
                if query:
                    normalized.append(
                        {
                            "label": f"variant_{index + 1}",
                            "query": query,
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            query = str(item.get("query", "")).strip()
            if not query:
                continue
            label = str(item.get("label") or f"variant_{index + 1}").strip()
            normalized.append({"label": label, "query": query})
        return normalized

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type
        self.range_seconds = self.DEFAULT_RANGE_SECONDS
        self.step = self.DEFAULT_STEP
        self.request_timeout_seconds = self.DEFAULT_TIMEOUT_SECONDS
        self.request_max_retries = self.DEFAULT_REQUEST_MAX_RETRIES
        self.request_retry_backoff_seconds = (
            self.DEFAULT_REQUEST_RETRY_BACKOFF_SECONDS
        )
        self.memory_range_seconds = self.DEFAULT_MEMORY_RANGE_SECONDS
        self.memory_step = self.DEFAULT_MEMORY_STEP
        self.memory_request_timeout_seconds = self.DEFAULT_MEMORY_TIMEOUT_SECONDS
        self.service_metric_fallback_range_seconds = (
            self.DEFAULT_SERVICE_METRIC_FALLBACK_RANGE_SECONDS
        )
        self.service_metric_fallback_step = self.DEFAULT_SERVICE_METRIC_FALLBACK_STEP
        self.memory_fallback_range_seconds = self.DEFAULT_MEMORY_FALLBACK_RANGE_SECONDS
        self.memory_fallback_step = self.DEFAULT_MEMORY_FALLBACK_STEP
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
            self.range_seconds = config.get(
                "range_seconds", self.DEFAULT_RANGE_SECONDS
            )
            self.step = config.get("step", self.DEFAULT_STEP)
            self.request_timeout_seconds = float(
                config.get("request_timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS)
            )
            self.request_max_retries = int(
                config.get(
                    "request_max_retries", self.DEFAULT_REQUEST_MAX_RETRIES
                )
            )
            self.request_retry_backoff_seconds = float(
                config.get(
                    "request_retry_backoff_seconds",
                    self.DEFAULT_REQUEST_RETRY_BACKOFF_SECONDS,
                )
            )
            self.memory_range_seconds = int(
                config.get("memory_range_seconds", self.DEFAULT_MEMORY_RANGE_SECONDS)
            )
            self.memory_step = config.get("memory_step", self.DEFAULT_MEMORY_STEP)
            self.memory_request_timeout_seconds = float(
                config.get(
                    "memory_request_timeout_seconds",
                    self.DEFAULT_MEMORY_TIMEOUT_SECONDS,
                )
            )
            self.service_metric_fallback_range_seconds = int(
                config.get(
                    "service_metric_fallback_range_seconds",
                    self.DEFAULT_SERVICE_METRIC_FALLBACK_RANGE_SECONDS,
                )
            )
            self.service_metric_fallback_step = config.get(
                "service_metric_fallback_step",
                self.DEFAULT_SERVICE_METRIC_FALLBACK_STEP,
            )
            self.memory_fallback_range_seconds = int(
                config.get(
                    "memory_fallback_range_seconds",
                    self.DEFAULT_MEMORY_FALLBACK_RANGE_SECONDS,
                )
            )
            self.memory_fallback_step = config.get(
                "memory_fallback_step",
                self.DEFAULT_MEMORY_FALLBACK_STEP,
            )

            (
                self.latency_query,
                self.latency_query_variants,
            ) = self._resolve_query_variants(
                config=config,
                query_key="latency_query",
                variants_key="latency_query_variants",
                default_variants=self._default_latency_query_variants(),
            )
            (
                self.error_rate_query,
                self.error_rate_query_variants,
            ) = self._resolve_query_variants(
                config=config,
                query_key="error_rate_query",
                variants_key="error_rate_query_variants",
                default_variants=self._default_error_rate_query_variants(),
            )
            (
                self.throughput_query,
                self.throughput_query_variants,
            ) = self._resolve_query_variants(
                config=config,
                query_key="throughput_query",
                variants_key="throughput_query_variants",
                default_variants=self._default_throughput_query_variants(),
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
        self.metrics_timeseries = self._empty_metrics_store()
        self.raw_metrics_timeseries = self._empty_metrics_store()
        self.metric_aliases = {}

        metric_queries = [
            (
                "latency",
                self.latency_query,
                self.range_seconds,
                self.step,
                self.request_timeout_seconds,
            ),
            (
                "error_rate",
                self.error_rate_query,
                self.range_seconds,
                self.step,
                self.request_timeout_seconds,
            ),
            (
                "throughput",
                self.throughput_query,
                self.range_seconds,
                self.step,
                self.request_timeout_seconds,
            ),
            (
                "cpu_usage",
                self.cpu_usage_query,
                self.range_seconds,
                self.step,
                self.request_timeout_seconds,
            ),
            (
                "memory_usage",
                self.memory_usage_query,
                self.memory_range_seconds,
                self.memory_step,
                self.memory_request_timeout_seconds,
            ),
        ]

        metric_errors = {}
        metric_fallbacks = {}
        metric_attempts = {}
        for metric_name, query, range_seconds, step, timeout_seconds in metric_queries:
            attempts = self._build_query_attempts(
                metric_name=metric_name,
                query=query,
                range_seconds=range_seconds,
                step=step,
                timeout_seconds=timeout_seconds,
            )
            metric_attempts[metric_name] = self._describe_metric_attempts(
                metric_name, attempts
            )

            last_error = None
            last_error_label = None
            metric_loaded = False
            query_variants = self._get_metric_query_variants(metric_name)
            if query_variants is not None:
                variant_result = self._load_service_metric_from_variants(
                    metric_name=metric_name,
                    attempts=attempts,
                    query_variants=query_variants,
                )
                metric_loaded = variant_result["succeeded"]
                last_error = variant_result["last_error"]
                last_error_label = variant_result["last_error_label"]
                fallback_info = variant_result.get("fallback")
                if fallback_info:
                    metric_fallbacks[metric_name] = fallback_info
            else:
                for attempt in attempts:
                    try:
                        metric_data = self._query_prometheus_range_with_retries(
                            query=attempt["query"],
                            range_seconds=attempt["range_seconds"],
                            step=attempt["step"],
                            timeout_seconds=attempt["timeout_seconds"],
                        )
                        extracted_timeseries = self._extract_timeseries(metric_data)
                        if not extracted_timeseries:
                            last_error_label = attempt["label"]
                            continue
                        self.metrics_timeseries.setdefault(metric_name, {}).update(
                            extracted_timeseries
                        )
                        metric_loaded = True
                        if attempt["label"] != "primary":
                            metric_fallbacks[metric_name] = {
                                "source": "degraded_query_window",
                                "attempt_label": attempt["label"],
                                "range_seconds": attempt["range_seconds"],
                                "step": attempt["step"],
                            }
                        break
                    except requests.exceptions.RequestException as e:
                        last_error = e
                        last_error_label = attempt["label"]

            if metric_loaded:
                continue

            cached_metric = copy.deepcopy(
                self.cached_metrics_timeseries.get(metric_name, {})
            )
            if cached_metric:
                self.metrics_timeseries[metric_name] = cached_metric
                metric_fallbacks[metric_name] = {
                    "source": "cached_previous_success",
                    "attempt_label": last_error_label,
                    "error": str(last_error) if last_error is not None else None,
                }
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Using cached Prometheus metric '{metric_name}' due to fetch failure: "
                            f"{last_error}"
                        ),
                    )
                )
                continue

            metric_errors[metric_name] = str(last_error)
            if last_error is not None:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.ERROR,
                        message=(
                            f"Failed to fetch Prometheus metric '{metric_name}': {last_error}"
                        ),
                    )
                )
            else:
                metric_errors[metric_name] = "Prometheus returned no usable time series"
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Prometheus metric '{metric_name}' returned no usable time series "
                            "for the configured query attempts."
                        ),
                    )
                )

        self.raw_metrics_timeseries = copy.deepcopy(self.metrics_timeseries)
        self.cached_metrics_timeseries = copy.deepcopy(self.metrics_timeseries)
        self.context.system_state.extra_attrs["prometheus_metric_errors"] = metric_errors
        self.context.system_state.extra_attrs["prometheus_metric_fallbacks"] = (
            metric_fallbacks
        )
        self.context.system_state.extra_attrs["prometheus_metric_attempts"] = (
            metric_attempts
        )
        self._update_graph_metrics()

    def _describe_metric_attempts(
        self, metric_name: str, attempts: list[dict]
    ) -> list[dict]:
        query_variants = self._get_metric_query_variants(metric_name)
        if query_variants is None:
            return [
                {
                    "label": attempt["label"],
                    "range_seconds": attempt["range_seconds"],
                    "step": attempt["step"],
                    "timeout_seconds": attempt["timeout_seconds"],
                }
                for attempt in attempts
            ]

        described_attempts = []
        for attempt in attempts:
            for variant in query_variants:
                described_attempts.append(
                    {
                        "label": attempt["label"],
                        "query_label": variant.get("label"),
                        "range_seconds": attempt["range_seconds"],
                        "step": attempt["step"],
                        "timeout_seconds": attempt["timeout_seconds"],
                    }
                )
        return described_attempts

    def _resolve_query_variants(
        self,
        config: dict,
        query_key: str,
        variants_key: str,
        default_variants: list[dict],
    ) -> tuple[str, list[dict]]:
        configured_variants = config.get(variants_key)
        configured_query = config.get(query_key)

        if configured_variants:
            normalized = self._normalize_query_variants(configured_variants)
            if normalized:
                return self._compose_or_query(normalized), normalized

        if configured_query is not None:
            configured_query_value = str(configured_query)
            return configured_query_value, [
                {
                    "label": f"configured_{query_key}",
                    "query": configured_query_value,
                }
            ]

        return self._compose_or_query(default_variants), default_variants

    def _get_metric_query_variants(self, metric_name: str) -> list[dict] | None:
        if metric_name == "latency":
            return self.latency_query_variants
        if metric_name == "error_rate":
            return self.error_rate_query_variants
        if metric_name == "throughput":
            return self.throughput_query_variants
        return None

    def _load_latency_metric(self, attempts: list[dict]) -> dict:
        return self._load_service_metric_from_variants(
            metric_name="latency",
            attempts=attempts,
            query_variants=self.latency_query_variants,
        )

    def _load_service_metric_from_variants(
        self,
        metric_name: str,
        attempts: list[dict],
        query_variants: list[dict],
    ) -> dict:
        collected_series = {}
        last_error = None
        last_error_label = None
        selected_attempt = None

        for attempt in attempts:
            for variant in query_variants:
                query_label = variant.get("label", metric_name)
                try:
                    metric_data = self._query_prometheus_range_with_retries(
                        query=variant["query"],
                        range_seconds=attempt["range_seconds"],
                        step=attempt["step"],
                        timeout_seconds=attempt["timeout_seconds"],
                    )
                    variant_series = self._extract_timeseries(metric_data)
                    if variant_series:
                        collected_series = variant_series
                        selected_attempt = {
                            "attempt_label": attempt["label"],
                            "query_label": query_label,
                            "range_seconds": attempt["range_seconds"],
                            "step": attempt["step"],
                        }
                        break
                except requests.exceptions.RequestException as e:
                    last_error = e
                    last_error_label = f"{attempt['label']}:{query_label}"

            if collected_series:
                break

        if collected_series:
            self.metrics_timeseries[metric_name] = collected_series

        fallback = None
        if selected_attempt and selected_attempt["attempt_label"] != "primary":
            fallback = {
                "source": "degraded_query_window",
                "attempt_label": selected_attempt["attempt_label"],
                "query_label": selected_attempt["query_label"],
                "range_seconds": selected_attempt["range_seconds"],
                "step": selected_attempt["step"],
            }
        elif selected_attempt:
            primary_variant = (
                query_variants[0].get("label")
                if query_variants
                else None
            )
            if selected_attempt["query_label"] != primary_variant:
                fallback = {
                    "source": "query_variant_fallback",
                    "attempt_label": selected_attempt["attempt_label"],
                    "query_label": selected_attempt["query_label"],
                    "range_seconds": selected_attempt["range_seconds"],
                    "step": selected_attempt["step"],
                }

        return {
            "succeeded": bool(collected_series),
            "last_error": last_error,
            "last_error_label": last_error_label,
            "fallback": fallback,
        }

    def _query_prometheus_range(
        self,
        query: str,
        range_seconds: int | None = None,
        step: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        url = f"{self.prometheus_url}/api/v1/query_range"
        resolved_step = step if step is not None else self.step
        step_seconds = self._parse_step_to_seconds(resolved_step)
        end_time = time.time()
        if step_seconds and step_seconds > 0:
            # Align query windows to Prometheus step boundaries. In practice this
            # avoids "successful but empty" range responses for sparse service
            # metrics when using arbitrary sub-second wall-clock timestamps.
            end_time = int(end_time // step_seconds) * step_seconds
        start_time = end_time - (
            range_seconds
            if range_seconds is not None
            else self.range_seconds
        )
        params = {
            "query": query,
            "start": start_time,
            "end": end_time,
            "step": resolved_step,
        }
        response = requests.get(
            url,
            params=params,
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else self.request_timeout_seconds
            ),
        )
        response.raise_for_status()
        return response.json()

    def _query_prometheus_range_with_retries(
        self,
        query: str,
        range_seconds: int | None = None,
        step: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        max_attempts = max(1, int(self.request_max_retries) + 1)
        last_error = None
        for attempt_index in range(max_attempts):
            try:
                return self._query_prometheus_range(
                    query=query,
                    range_seconds=range_seconds,
                    step=step,
                    timeout_seconds=timeout_seconds,
                )
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt_index + 1 >= max_attempts:
                    break
                time.sleep(max(0.0, self.request_retry_backoff_seconds))
        raise last_error

    def _build_query_attempts(
        self,
        metric_name: str,
        query: str,
        range_seconds: int,
        step: str,
        timeout_seconds: float,
    ) -> list[dict]:
        attempts = [
            {
                "label": "primary",
                "query": query,
                "range_seconds": range_seconds,
                "step": step,
                "timeout_seconds": timeout_seconds,
            }
        ]
        if metric_name not in {"latency", "error_rate", "throughput"}:
            if metric_name != "memory_usage":
                return attempts

            fallback_range_seconds = max(
                int(range_seconds),
                int(self.memory_fallback_range_seconds),
            )
            fallback_step = self._coarsen_step(step, self.memory_fallback_step)
            if (
                fallback_range_seconds == int(range_seconds)
                and fallback_step == step
            ):
                return attempts

            attempts.append(
                {
                    "label": "historical_window",
                    "query": query,
                    "range_seconds": fallback_range_seconds,
                    "step": fallback_step,
                    "timeout_seconds": timeout_seconds,
                }
            )
            return attempts

        fallback_range_seconds = min(
            int(range_seconds),
            int(self.service_metric_fallback_range_seconds),
        )
        fallback_step = self._coarsen_step(step, self.service_metric_fallback_step)
        if (
            fallback_range_seconds == int(range_seconds)
            and fallback_step == step
        ):
            return attempts

        attempts.append(
            {
                "label": "reduced_window",
                "query": query,
                "range_seconds": fallback_range_seconds,
                "step": fallback_step,
                "timeout_seconds": timeout_seconds,
            }
        )
        return attempts

    @staticmethod
    def _coarsen_step(current_step: str, fallback_step: str) -> str:
        current_step_seconds = PrometheusMetricsSource._parse_step_to_seconds(
            current_step
        )
        fallback_step_seconds = PrometheusMetricsSource._parse_step_to_seconds(
            fallback_step
        )
        if current_step_seconds is None or fallback_step_seconds is None:
            return fallback_step
        return (
            fallback_step
            if fallback_step_seconds > current_step_seconds
            else current_step
        )

    @staticmethod
    def _parse_step_to_seconds(step: str | None) -> int | None:
        if step is None:
            return None
        value = str(step).strip().lower()
        if not value:
            return None
        units = {
            "s": 1,
            "m": 60,
            "h": 3600,
        }
        suffix = value[-1]
        if suffix not in units:
            return None
        try:
            return int(float(value[:-1]) * units[suffix])
        except ValueError:
            return None

    def _update_timeseries(self, prometheus_response: dict, metric_name: str) -> None:
        extracted_timeseries = self._extract_timeseries(prometheus_response)
        self.metrics_timeseries.setdefault(metric_name, {}).update(extracted_timeseries)

    def _extract_timeseries(self, prometheus_response: dict) -> dict:
        if prometheus_response.get("status") != "success":
            return {}

        results = prometheus_response.get("data", {}).get("result", [])
        extracted = {}
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
                extracted[service_name] = timeseries
        return extracted

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
            "latency_variants": copy.deepcopy(self.latency_query_variants),
            "error_rate_variants": copy.deepcopy(self.error_rate_query_variants),
            "throughput_variants": copy.deepcopy(self.throughput_query_variants),
            "error_rate": self.error_rate_query.strip(),
            "throughput": self.throughput_query.strip(),
            "cpu_usage": self.cpu_usage_query.strip(),
            "memory_usage": self.memory_usage_query.strip(),
        }
        self.context.system_state.extra_attrs["prometheus_query_windows"] = {
            "default_range_seconds": self.range_seconds,
            "default_step": self.step,
            "default_timeout_seconds": self.request_timeout_seconds,
            "request_max_retries": self.request_max_retries,
            "request_retry_backoff_seconds": self.request_retry_backoff_seconds,
            "memory_range_seconds": self.memory_range_seconds,
            "memory_step": self.memory_step,
            "memory_timeout_seconds": self.memory_request_timeout_seconds,
            "memory_fallback_range_seconds": self.memory_fallback_range_seconds,
            "memory_fallback_step": self.memory_fallback_step,
            "service_metric_fallback_range_seconds": (
                self.service_metric_fallback_range_seconds
            ),
            "service_metric_fallback_step": self.service_metric_fallback_step,
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
            for graph_node in graph_nodes:
                if self._has_service_name_boundary_prefix(
                    metric_service_name, graph_node
                ):
                    candidates.append((len(normalized_graph_name), graph_node))
                    continue

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
    def _has_service_name_boundary_prefix(
        metric_service_name: str, graph_service_name: str
    ) -> bool:
        metric_value = str(metric_service_name or "").strip().lower()
        graph_value = str(graph_service_name or "").strip().lower()
        if not metric_value or not graph_value:
            return False
        if metric_value == graph_value:
            return True
        return any(
            metric_value.startswith(f"{graph_value}{separator}")
            for separator in ("-", "_", "/")
        )

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
