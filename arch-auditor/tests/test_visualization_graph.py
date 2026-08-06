import networkx as nx

from arch_auditor.visualization_graph import build_runtime_nodes, runtime_node_ids


def test_runtime_nodes_include_k8s_workloads_without_trace_edges():
    graph = nx.DiGraph()
    graph.add_edge("frontend", "checkout")
    extra_attrs = {
        "k8s_configs": {
            "deployments": [
                {"name": "frontend", "labels": {"app": "frontend"}},
                {"name": "checkout", "labels": {"app": "checkout"}},
                {"name": "payment", "labels": {"app": "payment"}},
            ],
            "pods": [],
        }
    }

    assert runtime_node_ids(graph, extra_attrs) == [
        "checkout",
        "frontend",
        "payment",
    ]

    nodes = {node["id"]: node for node in build_runtime_nodes(graph, extra_attrs)}
    assert nodes["frontend"]["trace_status"] == "recent"
    assert nodes["payment"]["trace_status"] == "no_recent_trace"
    assert nodes["payment"]["k8s_known"] is True
