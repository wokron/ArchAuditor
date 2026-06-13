import datetime
import statistics

import networkx as nx

from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class OverDecompositionAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "OverDecompositionAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["SingleRootDAGSource"]

    def init(self, config) -> bool:
        config = config or {}
        self.path_service_threshold = config.get("path_service_threshold", 5)
        self.pipe_service_ratio_threshold = config.get(
            "pipe_service_ratio_threshold", 0.3
        )
        self.co_deploy_overlap_threshold = config.get(
            "co_deploy_overlap_threshold", 0.8
        )
        self.co_deploy_window_minutes = config.get("co_deploy_window_minutes", 5)
        self.synthetic_root = config.get("synthetic_root", "<SOURCE>")
        return True

    def process(self) -> None:
        G = self.context.system_state.graph
        summary = {
            "root_services": [],
            "root_count": 0,
            "longest_path_services": [],
            "longest_path_service_count": 0,
            "path_service_threshold": self.path_service_threshold,
            "longest_path_total_avg_latency": None,
            "has_long_chain_issue": False,
            "pipe_services": [],
            "pipe_service_ratio": 0.0,
            "pipe_service_ratio_threshold": self.pipe_service_ratio_threshold,
            "has_pipe_service_ratio_issue": False,
            "co_deployed_pairs": [],
            "co_deploy_overlap_threshold": self.co_deploy_overlap_threshold,
        }

        if G.number_of_nodes() == 0:
            self.context.system_state.extra_attrs["over_decomposition_summary"] = summary
            return

        service_nodes = [node for node in G.nodes if node != self.synthetic_root]
        roots = [n for n in service_nodes if self._effective_in_degree(G, n) == 0]
        summary["root_services"] = [str(node) for node in roots]
        summary["root_count"] = len(roots)

        self._analyze_longest_path(G, summary)
        self._analyze_pipe_services(G, service_nodes, summary)
        self._analyze_co_deployment(G, service_nodes, summary)

        self.context.system_state.extra_attrs["over_decomposition_summary"] = summary

    def _analyze_longest_path(self, graph, summary: dict) -> None:
        try:
            longest_path = nx.dag_longest_path(graph)
        except Exception:
            return

        longest_path = [
            node for node in longest_path if node and node != self.synthetic_root
        ]
        summary["longest_path_services"] = [str(node) for node in longest_path]
        summary["longest_path_service_count"] = len(longest_path)
        summary["longest_path_total_avg_latency"] = self._estimate_path_latency(
            longest_path
        )

        if len(longest_path) > self.path_service_threshold:
            summary["has_long_chain_issue"] = True
            latency_hint = ""
            total_latency = summary["longest_path_total_avg_latency"]
            if total_latency is not None:
                latency_hint = (
                    f" Approximate cumulative average service latency along this path is "
                    f"{total_latency:.3f}."
                )
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        f"Long dependency chain detected: path {longest_path} contains "
                        f"{len(longest_path)} services, which exceeds the threshold of "
                        f"{self.path_service_threshold}.{latency_hint} "
                        "Consider simplifying the service interactions."
                    ),
                )
            )

    def _analyze_pipe_services(self, graph, service_nodes: list, summary: dict) -> None:
        pipe_services = []
        for node in service_nodes:
            predecessors = self._effective_predecessors(graph, node)
            successors = self._effective_successors(graph, node)
            if len(predecessors) == 1 and len(successors) == 1:
                pipe_services.append(
                    {
                        "service": str(node),
                        "upstream": str(predecessors[0]),
                        "downstream": str(successors[0]),
                    }
                )

        summary["pipe_services"] = pipe_services
        denominator = len(service_nodes)
        if denominator == 0:
            summary["pipe_service_ratio"] = 0.0
            return

        ratio = len(pipe_services) / denominator
        summary["pipe_service_ratio"] = ratio
        if ratio > self.pipe_service_ratio_threshold:
            summary["has_pipe_service_ratio_issue"] = True
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        f"High proportion of pipe services detected ({len(pipe_services)}/"
                        f"{denominator}, {ratio:.0%}). This may indicate over-decomposition. "
                        f"Review these tightly chained services: {[item['service'] for item in pipe_services]}"
                    ),
                )
            )

    def _analyze_co_deployment(self, graph, service_nodes: list, summary: dict) -> None:
        events = self.context.system_state.extra_attrs.get("deployment_history", [])
        if len(events) < 2:
            return

        service_times = self._build_deploy_times(events)
        seen_pairs = set()
        co_deployed_pairs = []

        for node in service_nodes:
            neighbors = set(self._effective_predecessors(graph, node)) | set(
                self._effective_successors(graph, node)
            )
            for neighbor in neighbors:
                pair = tuple(sorted((str(node), str(neighbor))))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                overlap = self._time_overlap(
                    service_times.get(str(node), []),
                    service_times.get(str(neighbor), []),
                    self.co_deploy_window_minutes,
                )
                if overlap < self.co_deploy_overlap_threshold:
                    continue

                pair_summary = {
                    "service_a": pair[0],
                    "service_b": pair[1],
                    "overlap_ratio": overlap,
                }
                co_deployed_pairs.append(pair_summary)
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.INFO,
                        message=(
                            f"Services '{pair[0]}' and '{pair[1]}' are directly dependent and "
                            f"deployed together {overlap:.0%} of the time. They may be split too finely."
                        ),
                    )
                )

        summary["co_deployed_pairs"] = co_deployed_pairs

    def _estimate_path_latency(self, services: list) -> float | None:
        metrics = self.context.system_state.extra_attrs.get("metrics_timeseries", {})
        latency_series = metrics.get("latency", {})
        samples = []
        for service in services:
            series = latency_series.get(service, [])
            values = [value for _, value in series if value is not None]
            if not values:
                continue
            samples.append(statistics.mean(values))
        if not samples:
            return None
        return sum(samples)

    def _effective_predecessors(self, graph, node) -> list:
        return [
            pred
            for pred in graph.predecessors(node)
            if pred != self.synthetic_root and pred is not None
        ]

    def _effective_successors(self, graph, node) -> list:
        return [
            succ
            for succ in graph.successors(node)
            if succ != self.synthetic_root and succ is not None
        ]

    def _effective_in_degree(self, graph, node) -> int:
        return len(self._effective_predecessors(graph, node))

    @staticmethod
    def _build_deploy_times(events: list[dict]) -> dict[str, list[datetime.datetime]]:
        service_times: dict[str, list[datetime.datetime]] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("action") != "deploy":
                continue
            service = event.get("service", "")
            ts_str = event.get("deployed_at", "")
            if not service or not ts_str:
                continue
            try:
                timestamp = datetime.datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            service_times.setdefault(service, []).append(timestamp)
        return service_times

    @staticmethod
    def _time_overlap(
        times_a: list[datetime.datetime],
        times_b: list[datetime.datetime],
        window_minutes: int,
    ) -> float:
        if not times_a or not times_b:
            return 0.0
        window = datetime.timedelta(minutes=window_minutes)

        overlap_a = 0
        for ta in times_a:
            if any(abs(ta - tb) <= window for tb in times_b):
                overlap_a += 1

        overlap_b = 0
        for tb in times_b:
            if any(abs(tb - ta) <= window for ta in times_a):
                overlap_b += 1

        ratio_a = overlap_a / len(times_a)
        ratio_b = overlap_b / len(times_b)
        return max(ratio_a, ratio_b)

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
