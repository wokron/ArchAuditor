from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.processors.sources.prometheus_metrics import PrometheusMetricsSource


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
