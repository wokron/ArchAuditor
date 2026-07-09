from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.processors.sources.prometheus_metrics import PrometheusMetricsSource
import requests


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_prometheus_metrics_source_maps_cpu_usage_from_container_name():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "cart")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "cpu_usage": {
                        "frontend": [(0, 0.02), (60, 0.03)],
                        "cart": [(0, 0.01), (60, 0.02)],
                    },
                },
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    metrics = auditor.system_state.extra_attrs["metrics_timeseries"]
    assert "cpu_usage" in metrics
    assert metrics["cpu_usage"]["frontend"] == [(0, 0.02), (60, 0.03)]
    assert metrics["cpu_usage"]["cart"] == [(0, 0.01), (60, 0.02)]
    assert auditor.system_state.graph.nodes["frontend"]["cpu_usage"] == [
        (0, 0.02),
        (60, 0.03),
    ]


def test_prometheus_metrics_source_maps_memory_usage():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "cart")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "memory_usage": {
                        "frontend": [(0, 100.0), (60, 120.0)],
                        "cart": [(0, 80.0), (60, 90.0)],
                    },
                },
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    metrics = auditor.system_state.extra_attrs["metrics_timeseries"]
    assert "memory_usage" in metrics
    assert metrics["memory_usage"]["frontend"] == [(0, 100.0), (60, 120.0)]
    assert metrics["memory_usage"]["cart"] == [(0, 80.0), (60, 90.0)]
    assert auditor.system_state.graph.nodes["frontend"]["memory_usage"] == [
        (0, 100.0),
        (60, 120.0),
    ]


def test_extract_metric_series_name_prefers_service_name():
    metric_labels = {
        "service_name": "frontend",
        "container_name": "k8s_frontend_frontend-abc_otel-demo_uid_1",
    }

    assert (
        PrometheusMetricsSource._extract_metric_series_name(metric_labels)
        == "frontend"
    )


def test_extract_metric_series_name_normalizes_k8s_container_name():
    metric_labels = {
        "container_name": "k8s_frontend-proxy_frontend-proxy-abc_otel-demo_uid_1",
    }

    assert (
        PrometheusMetricsSource._extract_metric_series_name(metric_labels)
        == "frontend-proxy"
    )


def test_prometheus_metrics_source_maps_short_service_name_from_pod_series():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [("frontend", "ad")],
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "cpu_usage": {
                        "ad-858b9bf84d-cbk8b": [(0, 0.01), (60, 0.02)],
                    },
                    "memory_usage": {
                        "ad-858b9bf84d-cbk8b": [(0, 100.0), (60, 120.0)],
                    },
                },
            },
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()

    metrics = auditor.system_state.extra_attrs["metrics_timeseries"]
    aliases = auditor.system_state.extra_attrs["metrics_aliases"]

    assert metrics["cpu_usage"]["ad"] == [(0, 0.01), (60, 0.02)]
    assert metrics["memory_usage"]["ad"] == [(0, 100.0), (60, 120.0)]
    assert aliases["ad-858b9bf84d-cbk8b"] == "ad"


def test_prometheus_metrics_source_keeps_other_metrics_when_memory_query_fails(
    monkeypatch,
):
    source = PrometheusMetricsSource(context=None)
    source.source_type = "Prometheus"
    source.prometheus_url = "http://localhost:9090"
    source.range_seconds = 3600
    source.step = "60s"
    source.request_timeout_seconds = 10.0
    source.memory_range_seconds = 1800
    source.memory_step = "300s"
    source.memory_request_timeout_seconds = 20.0
    source.latency_query = "latency"
    source.error_rate_query = "error"
    source.throughput_query = "throughput"
    source.cpu_usage_query = "cpu"
    source.memory_usage_query = "memory"
    source.metrics_timeseries = source._empty_metrics_store()
    source.raw_metrics_timeseries = source._empty_metrics_store()
    source.metric_aliases = {}

    class DummyReporter:
        def __init__(self):
            self.messages = []

        def report(self, report):
            self.messages.append(str(report))

    class DummySystemState:
        def __init__(self):
            self.graph = None
            self.extra_attrs = {}

    class DummyContext:
        def __init__(self):
            self.reporter = DummyReporter()
            self.system_state = DummySystemState()

    source.context = DummyContext()

    def fake_update_graph_metrics():
        source.context.system_state.extra_attrs["metrics_timeseries"] = dict(
            source.metrics_timeseries
        )

    source._update_graph_metrics = fake_update_graph_metrics

    success_response = {
        "status": "success",
        "data": {
            "result": [
                {
                    "metric": {"service_name": "frontend"},
                    "values": [[0, "1.0"], [60, "2.0"]],
                }
            ]
        },
    }

    def fake_query(query, range_seconds=None, step=None, timeout_seconds=None):
        if query == "memory":
            raise requests.exceptions.ReadTimeout("memory timeout")
        return success_response

    monkeypatch.setattr(source, "_query_prometheus_range", fake_query)

    source._process_prometheus()

    metrics = source.context.system_state.extra_attrs["metrics_timeseries"]
    assert metrics["latency"]["frontend"] == [(0, 1.0), (60, 2.0)]
    assert metrics["cpu_usage"]["frontend"] == [(0, 1.0), (60, 2.0)]
    assert metrics["memory_usage"] == {}
    assert (
        source.context.system_state.extra_attrs["prometheus_metric_errors"][
            "memory_usage"
        ]
        == "memory timeout"
    )
    assert any(
        "Failed to fetch Prometheus metric 'memory_usage'" in msg
        for msg in source.context.reporter.messages
    )


def test_latency_metric_uses_first_non_empty_variant_and_stops(monkeypatch):
    source = PrometheusMetricsSource(context=None)
    source.source_type = "Prometheus"
    source.prometheus_url = "http://localhost:9090"
    source.range_seconds = 900
    source.step = "60s"
    source.request_timeout_seconds = 20.0
    source.latency_query_variants = [
        {"label": "trace_span_metrics", "query": "trace_query"},
        {"label": "http_server_duration_milliseconds", "query": "http_ms_query"},
        {"label": "http_server_request_duration_seconds", "query": "http_sec_query"},
    ]
    source.metrics_timeseries = source._empty_metrics_store()

    query_calls = []
    responses = {
        "trace_query": {"status": "success", "data": {"result": []}},
        "http_ms_query": {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"service_name": "frontend"},
                        "values": [[0, "12.0"], [60, "13.0"]],
                    }
                ]
            },
        },
    }

    def fake_query(query, range_seconds=None, step=None, timeout_seconds=None):
        query_calls.append(query)
        return responses[query]

    monkeypatch.setattr(source, "_query_prometheus_range_with_retries", fake_query)

    result = source._load_latency_metric(
        [
            {
                "label": "primary",
                "range_seconds": 900,
                "step": "60s",
                "timeout_seconds": 20.0,
            }
        ]
    )

    assert result["succeeded"] is True
    assert result["fallback"]["source"] == "query_variant_fallback"
    assert result["fallback"]["query_label"] == "http_server_duration_milliseconds"
    assert source.metrics_timeseries["latency"]["frontend"] == [(0, 12.0), (60, 13.0)]
    assert query_calls == ["trace_query", "http_ms_query"]


def test_latency_metric_records_reduced_window_fallback(monkeypatch):
    source = PrometheusMetricsSource(context=None)
    source.source_type = "Prometheus"
    source.prometheus_url = "http://localhost:9090"
    source.range_seconds = 900
    source.step = "60s"
    source.request_timeout_seconds = 20.0
    source.latency_query_variants = [
        {"label": "trace_span_metrics", "query": "trace_query"},
    ]
    source.metrics_timeseries = source._empty_metrics_store()

    query_calls = []

    def fake_query(query, range_seconds=None, step=None, timeout_seconds=None):
        query_calls.append((query, range_seconds, step))
        if range_seconds == 900:
            raise requests.exceptions.ReadTimeout("primary timeout")
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"service_name": "checkout"},
                        "values": [[0, "21.0"], [120, "22.0"]],
                    }
                ]
            },
        }

    monkeypatch.setattr(source, "_query_prometheus_range_with_retries", fake_query)

    result = source._load_latency_metric(
        [
            {
                "label": "primary",
                "range_seconds": 900,
                "step": "60s",
                "timeout_seconds": 20.0,
            },
            {
                "label": "reduced_window",
                "range_seconds": 600,
                "step": "120s",
                "timeout_seconds": 20.0,
            },
        ]
    )

    assert result["succeeded"] is True
    assert result["fallback"]["source"] == "degraded_query_window"
    assert result["fallback"]["attempt_label"] == "reduced_window"
    assert source.metrics_timeseries["latency"]["checkout"] == [(0, 21.0), (120, 22.0)]
    assert query_calls == [
        ("trace_query", 900, "60s"),
        ("trace_query", 600, "120s"),
    ]


def test_error_rate_metric_uses_first_non_empty_variant_and_stops(monkeypatch):
    source = PrometheusMetricsSource(context=None)
    source.source_type = "Prometheus"
    source.prometheus_url = "http://localhost:9090"
    source.error_rate_query_variants = [
        {"label": "trace_span_metrics", "query": "trace_error_query"},
        {"label": "http_server_duration_milliseconds", "query": "http_error_query"},
    ]
    source.metrics_timeseries = source._empty_metrics_store()

    query_calls = []
    responses = {
        "trace_error_query": {"status": "success", "data": {"result": []}},
        "http_error_query": {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"service_name": "frontend"},
                        "values": [[0, "0.1"], [60, "0.2"]],
                    }
                ]
            },
        },
    }

    def fake_query(query, range_seconds=None, step=None, timeout_seconds=None):
        query_calls.append(query)
        return responses[query]

    monkeypatch.setattr(source, "_query_prometheus_range_with_retries", fake_query)

    result = source._load_service_metric_from_variants(
        metric_name="error_rate",
        attempts=[
            {
                "label": "primary",
                "range_seconds": 900,
                "step": "60s",
                "timeout_seconds": 20.0,
            }
        ],
        query_variants=source.error_rate_query_variants,
    )

    assert result["succeeded"] is True
    assert result["fallback"]["source"] == "query_variant_fallback"
    assert result["fallback"]["query_label"] == "http_server_duration_milliseconds"
    assert source.metrics_timeseries["error_rate"]["frontend"] == [
        (0, 0.1),
        (60, 0.2),
    ]
    assert query_calls == ["trace_error_query", "http_error_query"]


def test_throughput_metric_records_reduced_window_fallback(monkeypatch):
    source = PrometheusMetricsSource(context=None)
    source.source_type = "Prometheus"
    source.prometheus_url = "http://localhost:9090"
    source.throughput_query_variants = [
        {"label": "trace_span_metrics", "query": "trace_throughput_query"},
    ]
    source.metrics_timeseries = source._empty_metrics_store()

    query_calls = []

    def fake_query(query, range_seconds=None, step=None, timeout_seconds=None):
        query_calls.append((query, range_seconds, step))
        if range_seconds == 900:
            raise requests.exceptions.ReadTimeout("primary timeout")
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"service_name": "checkout"},
                        "values": [[0, "80"], [120, "90"]],
                    }
                ]
            },
        }

    monkeypatch.setattr(source, "_query_prometheus_range_with_retries", fake_query)

    result = source._load_service_metric_from_variants(
        metric_name="throughput",
        attempts=[
            {
                "label": "primary",
                "range_seconds": 900,
                "step": "60s",
                "timeout_seconds": 20.0,
            },
            {
                "label": "reduced_window",
                "range_seconds": 600,
                "step": "120s",
                "timeout_seconds": 20.0,
            },
        ],
        query_variants=source.throughput_query_variants,
    )

    assert result["succeeded"] is True
    assert result["fallback"]["source"] == "degraded_query_window"
    assert result["fallback"]["attempt_label"] == "reduced_window"
    assert source.metrics_timeseries["throughput"]["checkout"] == [
        (0, 80.0),
        (120, 90.0),
    ]
    assert query_calls == [
        ("trace_throughput_query", 900, "60s"),
        ("trace_throughput_query", 600, "120s"),
    ]
