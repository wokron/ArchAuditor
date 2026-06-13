from ...processors import Processor
from abc import ABC, abstractmethod
import html as html_lib
import json
import math
import time
import networkx as nx
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi import Request
from fastapi.responses import HTMLResponse
import requests
from arch_auditor.reporter import ReportMessage, ReportType
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import time
import json


class ServiceGraphSource(Processor):
    def __init__(self, context):
        super().__init__(context)
        self.source_type: str | None = None
        self.jaeger_url: str | None = None
        self.lookback_ms: int = 3600000  # Default: 1 hour in milliseconds

    @staticmethod
    def name() -> str:
        return "ServiceGraphSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        type = config.get("type", None)
        if type is None:
            return False

        self.source_type = type

        if type == "Mock":
            edges = config.get("edges", [])
            self.context.system_state.graph.add_edges_from(edges)
            return True
        elif type == "Jaeger":
            jaeger_url = config.get("jaeger_url", None)
            if jaeger_url is None:
                return False
            self.jaeger_url = jaeger_url
            self.lookback_ms = config.get("lookback_ms", 3600000)
            return True
        else:
            # Unknown type
            return False

    def process(self) -> None:
        if self.source_type == "Jaeger":
            self._process_jaeger()

    def _process_jaeger(self) -> None:
        try:
            now_ts = int(time.time() * 1000)
            end_ts = now_ts
            response = None
            for base_url in self._candidate_jaeger_urls():
                url = f"{base_url}/api/dependencies?lookback={self.lookback_ms}&endTs={end_ts}"
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
            edges = []

            # Jaeger dependencies API format:
            # {"data": [{"parent": "service1", "child": "service2", "callCount": 123}, ...]}
            callCounts = {}
            if "data" in dependencies:
                for dep in dependencies["data"]:
                    parent = dep.get("parent")
                    child = dep.get("child")
                    call_count = dep.get("callCount", 1)
                    if parent and child and parent != child:
                        edges.append((parent, child))
                        callCounts[child] = callCounts.get(child, 0) + call_count

            # Update graph structure
            if edges:
                self.context.system_state.graph.add_edges_from(edges)
                for node, count in callCounts.items():
                    self.context.system_state.graph.nodes[node]["call_count"] = count
                # Also store per-edge call counts for downstream analyzers
                if "data" in dependencies:
                    for dep in dependencies["data"]:
                        parent = dep.get("parent")
                        child = dep.get("child")
                        if parent and child and parent != child:
                            self.context.system_state.graph.edges[parent, child]["call_count"] = dep.get("callCount", 0)

        except requests.exceptions.RequestException as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Failed to fetch Jaeger dependencies: {e}",
                )
            )
        except Exception as e:
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=ReportType.ERROR,
                    message=f"Unexpected error while processing Jaeger dependencies: {e}",
                )
            )

    def _candidate_jaeger_urls(self) -> list[str]:
        base = (self.jaeger_url or "").rstrip("/")
        if base.endswith("/jaeger/ui"):
            return [base, base.removesuffix("/ui"), base.removesuffix("/jaeger/ui")]
        if base.endswith("/jaeger"):
            return [f"{base}/ui", base, base.removesuffix("/jaeger")]
        return [f"{base}/jaeger/ui", base]

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        # Get templates directory
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        
        # Convert NetworkX graph to G6 format
        G = self.context.system_state.graph
        
        nodes = []
        edges = []
        
        # Create nodes
        for node in G.nodes():
            node_data = {
                "id": str(node),
                "label": str(node),
            }
            
            # Add node attributes for styling
            node_attrs = G.nodes[node]
            if "priority" in node_attrs:
                priority = node_attrs["priority"]
                # Color nodes by priority (lower priority = more important = darker blue)
                if priority <= 1:
                    node_data["style"] = {"fill": "#1e40af"}  # Dark blue
                elif priority <= 3:
                    node_data["style"] = {"fill": "#3b82f6"}  # Medium blue
                else:
                    node_data["style"] = {"fill": "#93c5fd"}  # Light blue
            
            nodes.append(node_data)
        
        # Create edges
        for source, target in G.edges():
            edge_data = {
                "source": str(source),
                "target": str(target),
            }
            
            # Add edge attributes
            edge_attrs = G.edges[source, target]
            if "call_count" in edge_attrs:
                edge_data["label"] = f"calls: {edge_attrs['call_count']}"
            
            edges.append(edge_data)
        
        graph_data = {
            "nodes": nodes,
            "edges": edges,
        }
        
        # Prepare legend
        legend = [
            {"color": "#1e40af", "label": "高优先级服务 (priority ≤ 1)"},
            {"color": "#3b82f6", "label": "中优先级服务 (priority 2-3)"},
            {"color": "#93c5fd", "label": "低优先级服务 (priority > 3)"},
        ]
        
        return templates.TemplateResponse(
            "graph_visualization.html",
            {
                "request": {},  # Empty request object for compatibility
                "title": "服务依赖图",
                "description": "展示系统中各服务之间的依赖关系。节点代表服务，边代表调用关系。",
                "graph_data": json.dumps(graph_data),
                "legend": legend,
            },
        )
