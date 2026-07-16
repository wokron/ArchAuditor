from ...processors import Processor
import networkx as nx
import requests
import time
import math
from urllib.parse import quote
from arch_auditor.reporter import ReportMessage, ReportType


class ServiceDependencySource(Processor):
    """Annotates graph edges with dependency_type (strong / weak / unknown).

    Supported source types:
      - Mock   – explicit dependency list in config
      - Jaeger – infers strong/weak from Jaeger call counts
    """

    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.jaeger_url: str | None = None
        self.lookback_ms: int = 300000
        self.strong_threshold: int = 100
        self.correlation_threshold: float = 0.7
        self.min_aligned_points: int = 3
        self.trace_limit: int = 100

    @staticmethod
    def name() -> str:
        return "ServiceDependencySource"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource", "PrometheusMetricsSource"]

    def init(self, config: dict) -> bool:
        source_type = config.get("type", None)
        if source_type is None:
            return False
        self.source_type = source_type

        if source_type == "Mock":
            self.mock_deps = config.get("dependencies", [])
            return True
        elif source_type == "Jaeger":
            self.jaeger_url = config.get("jaeger_url", "")
            self.lookback_ms = config.get("lookback_ms", 300000)
            self.strong_threshold = config.get("strong_call_threshold", 100)
            self.correlation_threshold = config.get("correlation_threshold", 0.7)
            self.min_aligned_points = config.get("min_aligned_points", 3)
            self.trace_limit = config.get("trace_limit", 100)
            return bool(self.jaeger_url)
        else:
            return False

    def process(self) -> None:
        if self.source_type == "Mock":
            self._process_mock()
        elif self.source_type == "Jaeger":
            self._process_jaeger()

    def _process_mock(self) -> None:
        edge_attrs = {}
        for dep in self.mock_deps:
            from_service = dep.get("from")
            to_service = dep.get("to")
            dep_type = dep.get("type", "unknown")
            attrs = {"dependency_type": dep_type}
            if "call_count" in dep:
                attrs["call_count"] = dep.get("call_count")
            if "correlation" in dep:
                attrs["dependency_correlation"] = dep.get("correlation")
                attrs["dependency_correlation_status"] = dep.get(
                    "correlation_status", "ok"
                )
            elif "dependency_correlation" in dep:
                attrs["dependency_correlation"] = dep.get("dependency_correlation")
                attrs["dependency_correlation_status"] = dep.get(
                    "dependency_correlation_status", "ok"
                )
            edge_attrs[(from_service, to_service)] = attrs
        nx.set_edge_attributes(self.context.system_state.graph, edge_attrs)

    def _process_jaeger(self) -> None:
        try:
            now_ts = int(time.time() * 1000)
            response = None
            for base_url in self._candidate_jaeger_urls():
                url = f"{base_url}/api/dependencies?lookback={self.lookback_ms}&endTs={now_ts}"
                candidate = requests.get(url, timeout=10)
                candidate.raise_for_status()
                if "application/json" in (candidate.headers.get("Content-Type", "")):
                    response = candidate
                    break

            if response is None:
                raise requests.exceptions.RequestException(
                    "Failed to locate Jaeger JSON dependencies endpoint"
                )

            dependencies = response.json()
            if "data" not in dependencies:
                return

            G = self.context.system_state.graph
            threshold = self.strong_threshold
            metrics_timeseries = self.context.system_state.extra_attrs.get(
                "metrics_timeseries", {}
            )
            latency_timeseries = metrics_timeseries.get("latency", {})
            error_rate_timeseries = metrics_timeseries.get("error_rate", {})

            for dep in dependencies["data"]:
                parent = dep.get("parent")
                child = dep.get("child")
                call_count = dep.get("callCount", 0)
                if not parent or not child or parent == child:
                    continue
                if not G.has_edge(parent, child):
                    continue
                correlation_score = self._dependency_correlation(
                    latency_timeseries.get(parent, []),
                    latency_timeseries.get(child, []),
                    error_rate_timeseries.get(parent, []),
                    error_rate_timeseries.get(child, []),
                )
                dep_type = "strong" if (
                    call_count >= threshold
                    and correlation_score is not None
                    and correlation_score >= self.correlation_threshold
                ) else "weak"
                G.edges[parent, child]["dependency_type"] = dep_type
                G.edges[parent, child]["call_count"] = call_count
                G.edges[parent, child]["dependency_correlation"] = (
                    round(correlation_score, 4)
                    if correlation_score is not None
                    else None
                )
                G.edges[parent, child]["dependency_correlation_status"] = (
                    "ok" if correlation_score is not None else "insufficient_data"
                )

            # Any remaining edge without an explicit type stays "unknown"
            for u, v in G.edges:
                if "dependency_type" not in G.edges[u, v]:
                    G.edges[u, v]["dependency_type"] = "unknown"

            self._annotate_edge_durations(G)

        except requests.exceptions.RequestException as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                message=f"Failed to fetch Jaeger dependencies: {e}",
            )
        )

    def _annotate_edge_durations(self, graph: nx.DiGraph) -> None:
        if not graph.edges:
            return

        services = sorted({str(source) for source, _ in graph.edges})
        seen_trace_ids = set()
        durations_by_edge: dict[tuple[str, str], list[float]] = {}
        error_counts_by_edge: dict[tuple[str, str], int] = {}

        for service in services:
            traces = self._fetch_traces_for_service(service)
            for trace in traces:
                trace_id = trace.get("traceID")
                if trace_id and trace_id in seen_trace_ids:
                    continue
                if trace_id:
                    seen_trace_ids.add(trace_id)
                self._collect_trace_edge_stats(
                    trace, graph, durations_by_edge, error_counts_by_edge
                )

        for edge, durations in durations_by_edge.items():
            if not durations or not graph.has_edge(*edge):
                continue
            graph.edges[edge]["edge_span_count"] = len(durations)
            graph.edges[edge]["edge_avg_duration_ms"] = round(
                sum(durations) / len(durations), 3
            )
            graph.edges[edge]["edge_max_duration_ms"] = round(max(durations), 3)
            error_count = error_counts_by_edge.get(edge, 0)
            graph.edges[edge]["edge_error_count"] = error_count
            graph.edges[edge]["edge_error_rate"] = round(
                error_count / len(durations), 4
            )

    def _fetch_traces_for_service(self, service: str) -> list[dict]:
        lookback_minutes = max(1, math.ceil(self.lookback_ms / 60000))
        encoded_service = quote(service, safe="")

        for base_url in self._candidate_jaeger_urls():
            url = (
                f"{base_url}/api/traces"
                f"?service={encoded_service}"
                f"&lookback={lookback_minutes}m"
                f"&limit={self.trace_limit}"
            )
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            if "application/json" not in (response.headers.get("Content-Type", "")):
                continue
            payload = response.json()
            data = payload.get("data", [])
            return data if isinstance(data, list) else []

        return []

    def _collect_trace_edge_stats(
        self,
        trace: dict,
        graph: nx.DiGraph,
        durations_by_edge: dict[tuple[str, str], list[float]],
        error_counts_by_edge: dict[tuple[str, str], int],
    ) -> None:
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        if not isinstance(spans, list) or not isinstance(processes, dict):
            return

        span_by_id = {}
        service_by_span_id = {}
        for span in spans:
            if not isinstance(span, dict):
                continue
            span_id = span.get("spanID")
            if not span_id:
                continue
            span_by_id[span_id] = span
            process = processes.get(span.get("processID"), {})
            service_name = process.get("serviceName")
            if service_name:
                service_by_span_id[span_id] = str(service_name)

        for span in spans:
            child_span_id = span.get("spanID")
            child_service = service_by_span_id.get(child_span_id)
            if not child_service:
                continue
            references = span.get("references", []) or []
            for ref in references:
                if not isinstance(ref, dict):
                    continue
                if ref.get("refType") != "CHILD_OF":
                    continue
                parent_service = service_by_span_id.get(ref.get("spanID"))
                if (
                    not parent_service
                    or parent_service == child_service
                    or not graph.has_edge(parent_service, child_service)
                ):
                    continue
                duration_us = span.get("duration")
                if not isinstance(duration_us, (int, float)):
                    continue
                edge = (parent_service, child_service)
                durations_by_edge.setdefault(edge, []).append(duration_us / 1000.0)
                if self._span_has_error(span):
                    error_counts_by_edge[edge] = error_counts_by_edge.get(edge, 0) + 1

    @staticmethod
    def _span_has_error(span: dict) -> bool:
        for tag in span.get("tags", []) or []:
            if not isinstance(tag, dict):
                continue
            key = str(tag.get("key", "")).lower()
            value = tag.get("value")
            value_text = str(value).lower()

            if key == "error" and (
                value is True or value_text in {"true", "1", "yes"}
            ):
                return True
            if key in {"otel.status_code", "status.code"} and "error" in value_text:
                return True
            if key in {"http.status_code", "http.response.status_code"}:
                try:
                    if int(value) >= 500:
                        return True
                except (TypeError, ValueError):
                    continue

        return False

    def _candidate_jaeger_urls(self) -> list[str]:
        base = (self.jaeger_url or "").rstrip("/")
        if base.endswith("/jaeger/ui"):
            return [base, base.removesuffix("/ui"), base.removesuffix("/jaeger/ui")]
        if base.endswith("/jaeger"):
            return [f"{base}/ui", base, base.removesuffix("/jaeger")]
        return [f"{base}/jaeger/ui", base]

    def _dependency_correlation(
        self,
        parent_latency: list,
        child_latency: list,
        parent_error_rate: list,
        child_error_rate: list,
    ) -> float | None:
        latency_corr = self._pearson_from_timeseries(parent_latency, child_latency)
        error_corr = self._pearson_from_timeseries(parent_error_rate, child_error_rate)

        scores = [score for score in [latency_corr, error_corr] if score is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def _pearson_from_timeseries(self, lhs: list, rhs: list) -> float | None:
        lhs_map = self._timeseries_to_map(lhs)
        rhs_map = self._timeseries_to_map(rhs)
        common_timestamps = sorted(set(lhs_map.keys()) & set(rhs_map.keys()))
        if len(common_timestamps) < self.min_aligned_points:
            return None

        lhs_values = [lhs_map[ts] for ts in common_timestamps]
        rhs_values = [rhs_map[ts] for ts in common_timestamps]
        return self._pearson(lhs_values, rhs_values)

    @staticmethod
    def _timeseries_to_map(timeseries: list) -> dict:
        result = {}
        for point in timeseries or []:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            ts, value = point
            if value is None:
                continue
            result[ts] = value
        return result

    @staticmethod
    def _pearson(lhs: list[float], rhs: list[float]) -> float | None:
        if len(lhs) != len(rhs) or len(lhs) < 2:
            return None
        mean_lhs = sum(lhs) / len(lhs)
        mean_rhs = sum(rhs) / len(rhs)
        numerator = sum(
            (l - mean_lhs) * (r - mean_rhs) for l, r in zip(lhs, rhs)
        )
        denom_lhs = math.sqrt(sum((l - mean_lhs) ** 2 for l in lhs))
        denom_rhs = math.sqrt(sum((r - mean_rhs) ** 2 for r in rhs))
        denominator = denom_lhs * denom_rhs
        if denominator == 0:
            return None
        return numerator / denominator

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
