from __future__ import annotations

from collections.abc import Callable
from typing import Any

import networkx as nx


SYNTHETIC_ROOT = "<SOURCE>"


def k8s_workload_names(extra_attrs: dict[str, Any] | None) -> set[str]:
    """Return workload-like service names collected from Kubernetes state.

    Runtime analyzers should still use the traced service graph for their
    algorithms. Visualizations use this list as a background layer so deployed
    services with no recent Jaeger edges do not disappear from the graph.
    """

    extra_attrs = extra_attrs or {}
    k8s = extra_attrs.get("k8s_configs", {}) or {}
    names: set[str] = set()

    for dep in k8s.get("deployments", []) or []:
        if not isinstance(dep, dict):
            continue
        _add_name(names, dep.get("name"))
        labels = dep.get("labels") or {}
        for key in _SERVICE_LABEL_KEYS:
            _add_name(names, labels.get(key))

    for pod in k8s.get("pods", []) or []:
        if not isinstance(pod, dict):
            continue
        labels = pod.get("labels") or {}
        for key in _SERVICE_LABEL_KEYS:
            _add_name(names, labels.get(key))

    return names


def runtime_node_ids(
    graph: nx.DiGraph,
    extra_attrs: dict[str, Any] | None,
    synthetic_root: str = SYNTHETIC_ROOT,
) -> list[str]:
    graph_nodes = {
        str(node)
        for node in graph.nodes
        if node is not None and str(node) != synthetic_root
    }
    return sorted(graph_nodes | k8s_workload_names(extra_attrs))


def build_runtime_nodes(
    graph: nx.DiGraph,
    extra_attrs: dict[str, Any] | None,
    style_for_node: Callable[[str, bool, bool], dict[str, Any]] | None = None,
    label_for_node: Callable[[str], str] | None = None,
    size_for_node: Callable[[str, bool, bool], int] | None = None,
    synthetic_root: str = SYNTHETIC_ROOT,
) -> list[dict[str, Any]]:
    k8s_names = k8s_workload_names(extra_attrs)
    nodes = []
    for node_id in runtime_node_ids(graph, extra_attrs, synthetic_root):
        has_trace = graph.has_node(node_id)
        is_k8s_known = node_id in k8s_names
        style = (
            style_for_node(node_id, has_trace, is_k8s_known)
            if style_for_node
            else default_runtime_node_style(has_trace=has_trace)
        )
        size = (
            size_for_node(node_id, has_trace, is_k8s_known)
            if size_for_node
            else (56 if has_trace else 48)
        )
        nodes.append(
            {
                "id": node_id,
                "label": label_for_node(node_id) if label_for_node else node_id,
                "style": style,
                "size": size,
                "trace_status": "recent" if has_trace else "no_recent_trace",
                "k8s_known": is_k8s_known,
                "description": (
                    "recent Jaeger trace data"
                    if has_trace
                    else "deployed in Kubernetes, no recent Jaeger edge"
                ),
            }
        )
    return nodes


def default_runtime_node_style(has_trace: bool = True) -> dict[str, Any]:
    if has_trace:
        return {"fill": "#cbd5e1", "stroke": "#64748b", "lineWidth": 1.5}
    return {
        "fill": "#e2e8f0",
        "stroke": "#94a3b8",
        "lineWidth": 1.5,
        "lineDash": [5, 4],
        "opacity": 0.9,
    }


def trace_edge_label(attrs: dict[str, Any] | None) -> str | None:
    attrs = attrs or {}
    call_count = attrs.get("call_count") or attrs.get("callCount")
    if call_count is not None:
        return f"calls: {call_count}"
    return None


def _add_name(names: set[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text or text == SYNTHETIC_ROOT or text == "kubernetes":
        return
    names.add(text)


_SERVICE_LABEL_KEYS = (
    "app",
    "app.kubernetes.io/name",
    "service",
    "service_name",
    "app.kubernetes.io/component",
)
