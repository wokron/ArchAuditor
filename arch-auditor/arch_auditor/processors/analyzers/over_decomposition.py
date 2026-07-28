import json
import datetime
import statistics
from pathlib import Path

import networkx as nx
from fastapi.templating import Jinja2Templates

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
        self.fanout_amplification_threshold = config.get(
            "fanout_amplification_threshold", 2.0
        )
        self.min_upstream_calls = config.get("min_upstream_calls", 5)
        self.co_deploy_overlap_threshold = config.get(
            "co_deploy_overlap_threshold", 0.8
        )
        self.co_deploy_window_minutes = config.get("co_deploy_window_minutes", 5)
        self.synthetic_root = config.get("synthetic_root", "<SOURCE>")
        self.long_path_limit = int(config.get("long_path_limit", 5))
        self.max_scanned_paths = int(config.get("max_scanned_paths", 10000))
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
            "top_long_paths": [],
            "long_path_count": 0,
            "long_path_limit": self.long_path_limit,
            "has_long_chain_issue": False,
            "pipe_services": [],
            "pipe_service_ratio": 0.0,
            "pipe_service_ratio_threshold": self.pipe_service_ratio_threshold,
            "has_pipe_service_ratio_issue": False,
            "fanout_amplification_services": [],
            "fanout_amplification_threshold": self.fanout_amplification_threshold,
            "has_fanout_amplification_issue": False,
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
        self._analyze_fanout_amplification(G, service_nodes, summary)
        self._analyze_co_deployment(G, service_nodes, summary)

        self.context.system_state.extra_attrs["over_decomposition_summary"] = summary

    def _analyze_longest_path(self, graph, summary: dict) -> None:
        ranked_paths = self._find_ranked_simple_service_paths(graph)
        longest_path = ranked_paths[0] if ranked_paths else []
        summary["longest_path_services"] = [str(node) for node in longest_path]
        summary["longest_path_service_count"] = len(longest_path)
        summary["longest_path_total_avg_latency"] = self._estimate_path_latency(
            longest_path
        )

        long_paths = [
            path
            for path in ranked_paths
            if len(path) >= self.path_service_threshold
        ]
        summary["long_path_count"] = len(long_paths)
        summary["top_long_paths"] = [
            {
                "rank": index,
                "services": [str(node) for node in path],
                "service_count": len(path),
                "total_avg_latency": self._estimate_path_latency(path),
            }
            for index, path in enumerate(
                long_paths[: self.long_path_limit], start=1
            )
        ]

        if long_paths:
            summary["has_long_chain_issue"] = True
            latency_hint = ""
            total_latency = summary["longest_path_total_avg_latency"]
            if total_latency is not None:
                latency_hint = (
                    f" Approximate cumulative average service latency along the longest path is "
                    f"{total_latency:.3f}."
                )
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        f"Long dependency chains detected: {len(long_paths)} path(s) reach "
                        f"the threshold of {self.path_service_threshold}. "
                        f"Longest path {longest_path} contains "
                        f"{len(longest_path)} services, which exceeds the threshold of "
                        f"{self.path_service_threshold}.{latency_hint} "
                        "Consider simplifying the service interactions."
                    ),
                )
            )

    def _find_longest_simple_service_path(self, graph) -> list:
        ranked_paths = self._find_ranked_simple_service_paths(graph)
        return ranked_paths[0] if ranked_paths else []

    def _find_ranked_simple_service_paths(self, graph) -> list[list]:
        service_nodes = [
            node for node in graph.nodes if node and node != self.synthetic_root
        ]
        if not service_nodes:
            return []

        roots = [
            node for node in service_nodes if self._effective_in_degree(graph, node) == 0
        ]
        sinks = [
            node
            for node in service_nodes
            if len(self._effective_successors(graph, node)) == 0
        ]
        starts = roots or service_nodes
        ends = sinks or service_nodes

        path_by_key = {}
        scanned_paths = 0
        cutoff = len(service_nodes)

        for start in starts:
            for end in ends:
                if start == end:
                    continue
                try:
                    simple_paths = nx.all_simple_paths(
                        graph, start, end, cutoff=cutoff
                    )
                    for path in simple_paths:
                        path = [
                            node
                            for node in path
                            if node and node != self.synthetic_root
                        ]
                        if len(path) >= 2:
                            path_by_key.setdefault(tuple(path), path)
                        scanned_paths += 1
                        if scanned_paths >= self.max_scanned_paths:
                            return self._rank_paths(path_by_key.values())
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue

        if path_by_key:
            return self._rank_paths(path_by_key.values())

        for source, target in graph.edges:
            path = [
                node
                for node in (source, target)
                if node and node != self.synthetic_root
            ]
            if len(path) >= 2:
                path_by_key.setdefault(tuple(path), path)
        return self._rank_paths(path_by_key.values())

    def _rank_paths(self, paths) -> list[list]:
        return sorted(
            paths,
            key=lambda path: (
                -len(path),
                -(self._estimate_path_latency(path) or 0.0),
                " -> ".join(str(node) for node in path),
            ),
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

    def _analyze_fanout_amplification(
        self, graph, service_nodes: list, summary: dict
    ) -> None:
        amplified_services = []
        for node in service_nodes:
            upstream_edges = []
            for predecessor in self._effective_predecessors(graph, node):
                call_count = graph.edges[predecessor, node].get("call_count")
                if call_count is None:
                    continue
                upstream_edges.append((predecessor, call_count))

            downstream_edges = []
            for successor in self._effective_successors(graph, node):
                call_count = graph.edges[node, successor].get("call_count")
                if call_count is None:
                    continue
                downstream_edges.append((successor, call_count))

            if not upstream_edges or not downstream_edges:
                continue

            upstream_service, upstream_calls = max(
                upstream_edges, key=lambda item: item[1]
            )
            if upstream_calls < self.min_upstream_calls:
                continue

            downstream_service, downstream_calls = max(
                downstream_edges, key=lambda item: item[1]
            )
            if downstream_calls <= 0:
                continue

            amplification_ratio = downstream_calls / upstream_calls
            if amplification_ratio < self.fanout_amplification_threshold:
                continue

            amplified = {
                "service": str(node),
                "upstream": str(upstream_service),
                "upstream_call_count": upstream_calls,
                "downstream": str(downstream_service),
                "downstream_call_count": downstream_calls,
                "amplification_ratio": round(amplification_ratio, 2),
            }
            amplified_services.append(amplified)
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.WARNING,
                    message=(
                        f"Fan-out amplification detected on service '{node}': "
                        f"upstream '{upstream_service}' observed {upstream_calls} calls, "
                        f"but downstream '{downstream_service}' observed "
                        f"{downstream_calls} calls ({amplification_ratio:.2f}x). "
                        "This may indicate N+1 calls or excessive decomposition."
                    ),
                )
            )

        summary["fanout_amplification_services"] = amplified_services
        summary["has_fanout_amplification_issue"] = bool(amplified_services)

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
        return True

    def visualize(self):
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))

        graph = self.context.system_state.graph
        summary = (
            self.context.system_state.extra_attrs.get(
                "over_decomposition_summary", {}
            )
            or {}
        )

        top_long_paths = summary.get("top_long_paths", []) or []
        long_path_lists = []
        for item in top_long_paths:
            services = item.get("services", []) if isinstance(item, dict) else []
            if services:
                long_path_lists.append([str(service) for service in services])

        longest_path = [str(service) for service in summary.get("longest_path_services", []) or []]
        if not long_path_lists and longest_path:
            long_path_lists.append(longest_path)

        long_path_nodes = {
            node for path in long_path_lists for node in path
        }
        long_path_edges = set()
        for path in long_path_lists:
            long_path_edges.update(zip(path, path[1:]))

        pipe_services = summary.get("pipe_services", []) or []
        pipe_nodes = {item.get("service") for item in pipe_services}
        pipe_edges = set()
        for item in pipe_services:
            service = item.get("service")
            upstream = item.get("upstream")
            downstream = item.get("downstream")
            if upstream and service:
                pipe_edges.add((upstream, service))
            if service and downstream:
                pipe_edges.add((service, downstream))

        fanout_services = summary.get("fanout_amplification_services", []) or []
        fanout_nodes = {item.get("service") for item in fanout_services}
        fanout_edges = set()
        for item in fanout_services:
            service = item.get("service")
            upstream = item.get("upstream")
            downstream = item.get("downstream")
            if upstream and service:
                fanout_edges.add((upstream, service))
            if service and downstream:
                fanout_edges.add((service, downstream))

        co_deployed_pairs = summary.get("co_deployed_pairs", []) or []
        co_deployed_nodes = set()
        co_deployed_edges = set()
        for item in co_deployed_pairs:
            service_a = item.get("service_a")
            service_b = item.get("service_b")
            if not service_a or not service_b:
                continue
            co_deployed_nodes.update([service_a, service_b])
            co_deployed_edges.add((service_a, service_b))
            co_deployed_edges.add((service_b, service_a))

        def node_style(node_id: str) -> dict:
            if node_id in long_path_nodes:
                return {
                    "fill": "#ef4444",
                    "stroke": "#991b1b",
                    "lineWidth": 3,
                }
            if node_id in fanout_nodes:
                return {
                    "fill": "#8b5cf6",
                    "stroke": "#5b21b6",
                    "lineWidth": 3,
                }
            if node_id in pipe_nodes:
                return {
                    "fill": "#f59e0b",
                    "stroke": "#b45309",
                    "lineWidth": 2,
                }
            if node_id in co_deployed_nodes:
                return {
                    "fill": "#14b8a6",
                    "stroke": "#0f766e",
                    "lineWidth": 2,
                }
            return {
                "fill": "#cbd5e1",
                "stroke": "#64748b",
                "lineWidth": 1.5,
            }

        nodes = []
        for node in graph.nodes():
            node_id = str(node)
            if node_id == self.synthetic_root:
                continue
            nodes.append(
                {
                    "id": node_id,
                    "label": node_id,
                    "style": node_style(node_id),
                    "size": 70 if node_id in long_path_nodes else 60,
                }
            )

        def edge_style(edge_key: tuple[str, str]) -> tuple[dict, str | None]:
            if edge_key in long_path_edges:
                return (
                    {
                        "stroke": "#ef4444",
                        "lineWidth": 4,
                        "endArrow": {
                            "path": "M 0,0 L 10,4 L 10,-4 Z",
                            "fill": "#ef4444",
                        },
                    },
                    "long",
                )
            if edge_key in fanout_edges:
                return (
                    {
                        "stroke": "#8b5cf6",
                        "lineWidth": 3,
                        "endArrow": {
                            "path": "M 0,0 L 10,4 L 10,-4 Z",
                            "fill": "#8b5cf6",
                        },
                    },
                    "fanout",
                )
            if edge_key in pipe_edges:
                return (
                    {
                        "stroke": "#f59e0b",
                        "lineWidth": 3,
                        "endArrow": {
                            "path": "M 0,0 L 10,4 L 10,-4 Z",
                            "fill": "#f59e0b",
                        },
                    },
                    "pipe",
                )
            if edge_key in co_deployed_edges:
                return (
                    {
                        "stroke": "#14b8a6",
                        "lineWidth": 2.5,
                        "lineDash": [6, 4],
                        "endArrow": {
                            "path": "M 0,0 L 10,4 L 10,-4 Z",
                            "fill": "#14b8a6",
                        },
                    },
                    "co-deploy",
                )
            return ({}, None)

        edges = []
        for source, target, attrs in graph.edges(data=True):
            source_id = str(source)
            target_id = str(target)
            if source_id == self.synthetic_root or target_id == self.synthetic_root:
                continue
            style, label = edge_style((source_id, target_id))
            edge_data = {
                "source": source_id,
                "target": target_id,
            }
            if style:
                edge_data["style"] = style
            if label:
                edge_data["label"] = label
            elif attrs.get("call_count") is not None:
                edge_data["label"] = f"calls: {attrs.get('call_count')}"
            edges.append(edge_data)

        def format_latency(value) -> str:
            if value is None:
                return "无耗时数据"
            return f"累计平均耗时 {value:.1f} ms"

        path_descriptions = []
        for index, item in enumerate(top_long_paths[: self.long_path_limit], start=1):
            services = [str(service) for service in item.get("services", [])]
            if not services:
                continue
            path_descriptions.append(
                (
                    f"链路 {index}: {' -> '.join(services)} "
                    f"({item.get('service_count', len(services))} 个服务, "
                    f"{format_latency(item.get('total_avg_latency'))})"
                )
            )

        if not path_descriptions and longest_path:
            path_descriptions.append(
                (
                    f"最长链路: {' -> '.join(longest_path)} "
                    f"({summary.get('longest_path_service_count', len(longest_path))} 个服务, "
                    f"{format_latency(summary.get('longest_path_total_avg_latency'))})"
                )
            )

        paths_text = (
            "；".join(path_descriptions)
            if path_descriptions
            else "当前未发现超过阈值的长调用链"
        )
        description = (
            "展示过度拆分相关信号：红色表示达到阈值的长调用链；"
            "橙色管道服务表示只有一个上游和一个下游的中转型服务，容易把功能拆得过细；"
            "紫色扇出放大服务表示一个上游请求被该服务放大为多次下游调用，常见于 N+1 查询；"
            "青色表示存在直接依赖且经常共同部署的服务对。 "
            f"长调用链阈值为 {summary.get('path_service_threshold')} 个服务；"
            f"当前发现 {summary.get('long_path_count', 0)} 条，"
            f"最多展示 {summary.get('long_path_limit', self.long_path_limit)} 条：{paths_text}。"
        )

        legend = [
            {"color": "#ef4444", "label": "最长调用链"},
            {"color": "#8b5cf6", "label": "扇出放大服务"},
            {"color": "#f59e0b", "label": "管道服务"},
            {"color": "#14b8a6", "label": "共同部署服务对"},
            {"color": "#cbd5e1", "label": "其他服务"},
        ]

        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},
                "title": "过度拆分分析",
                "description": description,
                "graph_data": json.dumps({"nodes": nodes, "edges": edges}),
                "legend": legend,
            },
        )
