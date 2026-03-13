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
            url = f"{self.jaeger_url}/api/dependencies?lookback={self.lookback_ms}&endTs={end_ts}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            dependencies = response.json()
            edges = []

            # Jaeger dependencies API format:
            # {"data": [{"parent": "service1", "child": "service2", "callCount": 123}, ...]}
            if "data" in dependencies:
                for dep in dependencies["data"]:
                    parent = dep.get("parent")
                    child = dep.get("child")
                    if parent and child and parent != child:
                        edges.append((parent, child))

            # Update graph structure
            if edges:
                self.context.system_state.graph.add_edges_from(edges)

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

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self, request: Request):
        graph = self.context.system_state.graph
        query_params = request.query_params if request else {}

        def normalize_list(value: str | None) -> list[str]:
            if not value:
                return []
            return [item.strip() for item in value.split(",") if item.strip()]

        def get_list(primary: str, fallback: str) -> list[str]:
            items = []
            if request:
                items = query_params.getlist(primary)
            if not items:
                items = normalize_list(query_params.get(primary))
            if not items and fallback:
                if request:
                    items = query_params.getlist(fallback)
                if not items:
                    items = normalize_list(query_params.get(fallback))
            return items

        services = get_list("service", "svc")
        direction = (
            query_params.get("direction") or query_params.get("dir") or "both"
        ).lower()
        if direction not in {"both", "upstream", "downstream"}:
            direction = "both"
        layout = (query_params.get("layout") or "auto").lower()
        if layout not in {"auto", "spring", "circular", "kamada"}:
            layout = "auto"
        embed = str(query_params.get("embed") or "").lower() in {"1", "true", "yes"}
        if not embed:
            embed = (query_params.get("mode") or "").lower() == "embed"

        try:
            depth = int(query_params.get("depth") or "2")
        except ValueError:
            depth = 2
        depth = max(0, min(depth, 5))

        def collect_neighbors(root: str, depth_value: int) -> set[str]:
            visited = {root}
            frontier = {root}
            for _ in range(depth_value):
                next_nodes: set[str] = set()
                for node in frontier:
                    if direction in {"downstream", "both"}:
                        next_nodes.update(graph.successors(node))
                    if direction in {"upstream", "both"}:
                        next_nodes.update(graph.predecessors(node))
                next_nodes -= visited
                visited.update(next_nodes)
                frontier = next_nodes
            return visited

        if graph.number_of_nodes() == 0:
            if embed:
                html = """
                <!DOCTYPE html>
                <html lang="en">
                  <head>
                    <meta charset="utf-8" />
                    <meta name="viewport" content="width=device-width, initial-scale=1" />
                    <title>Service Graph</title>
                    <style>
                      html, body { margin: 0; width: 100%; height: 100%; }
                      body {
                        font-family: "Segoe UI", Arial, sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        color: #0f172a;
                        background: transparent;
                      }
                      .empty { padding: 16px 20px; border: 1px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; }
                    </style>
                  </head>
                  <body>
                    <div class="empty">No graph data available yet. Trigger an audit or check the data source.</div>
                  </body>
                </html>
                """
                return HTMLResponse(content=html)
            html = """
            <!DOCTYPE html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Service Graph</title>
                <style>
                  body { font-family: "Segoe UI", Arial, sans-serif; padding: 24px; color: #0f172a; }
                  .empty { padding: 20px; border: 1px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; }
                </style>
              </head>
              <body>
                <h1>Service Graph</h1>
                <div class="empty">No graph data available yet. Trigger an audit or check the data source.</div>
              </body>
            </html>
            """
            return HTMLResponse(content=html)

        subgraph = graph
        missing_services = [service for service in services if service not in graph]
        if services:
            nodes: set[str] = set()
            for service in services:
                if service not in graph:
                    continue
                nodes.update(collect_neighbors(service, depth))
            if nodes:
                subgraph = graph.subgraph(nodes).copy()
            else:
                subgraph = graph.copy()

        limited = False
        max_nodes = 200
        if subgraph.number_of_nodes() > max_nodes:
            limited = True
            ranked = sorted(subgraph.degree, key=lambda item: item[1], reverse=True)
            keep = {node for node, _ in ranked[:max_nodes]}
            subgraph = subgraph.subgraph(keep).copy()

        node_count = subgraph.number_of_nodes()
        edge_count = subgraph.number_of_edges()

        if node_count == 1:
            positions = {next(iter(subgraph.nodes)): (0.0, 0.0)}
        else:
            if layout == "circular" or (layout == "auto" and node_count > 80):
                positions = nx.circular_layout(subgraph)
            elif layout == "kamada":
                positions = nx.kamada_kawai_layout(subgraph)
            else:
                k = 1 / math.sqrt(node_count)
                positions = nx.spring_layout(subgraph, seed=42, k=k)

        width = 1000
        height = 600
        padding = 60
        xs = [pos[0] for pos in positions.values()]
        ys = [pos[1] for pos in positions.values()]
        min_x = min(xs) if xs else 0
        max_x = max(xs) if xs else 1
        min_y = min(ys) if ys else 0
        max_y = max(ys) if ys else 1
        span_x = max_x - min_x if max_x != min_x else 1
        span_y = max_y - min_y if max_y != min_y else 1

        def scale_x(value: float) -> float:
            return padding + (value - min_x) / span_x * (width - 2 * padding)

        def scale_y(value: float) -> float:
            return padding + (value - min_y) / span_y * (height - 2 * padding)

        nodes = []
        for node in subgraph.nodes:
            pos = positions.get(node, (0.0, 0.0))
            nodes.append(
                {
                    "id": str(node),
                    "x": round(scale_x(pos[0]), 2),
                    "y": round(scale_y(pos[1]), 2),
                    "in": int(subgraph.in_degree(node)),
                    "out": int(subgraph.out_degree(node)),
                    "degree": int(subgraph.degree(node)),
                }
            )

        edges = []
        max_weight = 1
        for source, target, data in subgraph.edges(data=True):
            weight = data.get("call_count") or data.get("callCount") or 1
            try:
                weight = int(weight)
            except (TypeError, ValueError):
                weight = 1
            if weight > max_weight:
                max_weight = weight
            edges.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "weight": weight,
                    "dependency": str(data.get("dependency_type", "")),
                }
            )

        data = {
            "nodes": nodes,
            "edges": edges,
            "width": width,
            "height": height,
            "maxWeight": max_weight,
            "highlight": services,
        }
        data_json = json.dumps(data).replace("</", "<\\/")

        def escape(value: str) -> str:
            return html_lib.escape(value or "")

        service_value = ", ".join(services)
        missing_note = ""
        if missing_services:
            missing_note = f"Missing services: {escape(', '.join(missing_services))}"
        limit_note = "Graph trimmed to top 200 nodes by degree." if limited else ""

        direction_options = "".join(
            [
                f"<option value='{option}'{' selected' if option == direction else ''}>{option.title()}</option>"
                for option in ["both", "upstream", "downstream"]
            ]
        )
        depth_options = "".join(
            [
                f"<option value='{value}'{' selected' if value == depth else ''}>{value}</option>"
                for value in range(0, 6)
            ]
        )
        layout_options = "".join(
            [
                f"<option value='{value}'{' selected' if value == layout else ''}>{value.title()}</option>"
                for value in ["auto", "spring", "circular", "kamada"]
            ]
        )

        edge_rows = []
        for edge in edges[:200]:
            edge_rows.append(
                f"<tr><td>{escape(edge['source'])}</td><td>{escape(edge['target'])}</td><td>{edge['weight']}</td><td>{escape(edge['dependency'])}</td></tr>"
            )
        edge_table = (
            """
            <div class="table-card">
              <h3>Edges (sample)</h3>
              <table>
                <thead><tr><th>Source</th><th>Target</th><th>Weight</th><th>Type</th></tr></thead>
                <tbody>
            """
            + "".join(edge_rows)
            + """
                </tbody>
              </table>
            </div>
            """
            if edge_rows
            else ""
        )

        body_class = "embed" if embed else ""
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>Service Graph</title>
            <style>
              :root {{
                --bg: #f8fafc;
                --card: #ffffff;
                --border: #e2e8f0;
                --text: #0f172a;
                --muted: #64748b;
                --accent: #2563eb;
                --accent-soft: rgba(37, 99, 235, 0.12);
              }}
              * {{ box-sizing: border-box; }}
              body {{
                margin: 0;
                font-family: "Segoe UI", Arial, sans-serif;
                background: var(--bg);
                color: var(--text);
                padding: 24px;
              }}
              .container {{
                max-width: 1200px;
                margin: 0 auto;
                display: flex;
                flex-direction: column;
                gap: 16px;
              }}
              h1 {{
                margin: 0;
                font-size: 24px;
              }}
              .meta {{
                color: var(--muted);
                font-size: 14px;
              }}
              .filters {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 16px;
              }}
              .filters label {{
                display: block;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: var(--muted);
                margin-bottom: 6px;
              }}
              .filters input, .filters select, .filters button, .filters a {{
                width: 100%;
                padding: 8px 10px;
                border-radius: 8px;
                border: 1px solid var(--border);
                font-size: 14px;
                background: #fff;
                color: var(--text);
                text-decoration: none;
              }}
              .filters button {{
                background: var(--accent);
                color: #fff;
                border-color: var(--accent);
                font-weight: 600;
              }}
              .filters .actions {{
                display: flex;
                gap: 8px;
                align-items: flex-end;
              }}
              .notice {{
                background: var(--accent-soft);
                border: 1px solid rgba(37, 99, 235, 0.3);
                padding: 10px 12px;
                border-radius: 10px;
                font-size: 13px;
                color: var(--text);
              }}
              .graph-card {{
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 12px;
                min-height: 640px;
              }}
              svg {{
                width: 100%;
                height: 600px;
              }}
              .node circle {{
                fill: #ffffff;
                stroke: var(--accent);
                stroke-width: 1.5;
              }}
              .node text {{
                font-size: 12px;
                fill: var(--text);
              }}
              .edge {{
                stroke: #94a3b8;
                stroke-opacity: 0.7;
              }}
              .edge.highlight {{
                stroke: var(--accent);
                stroke-opacity: 1;
              }}
              .node.highlight circle {{
                fill: var(--accent-soft);
                stroke-width: 2.5;
              }}
              .table-card {{
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 16px;
              }}
              table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
              }}
              th, td {{
                padding: 8px 10px;
                border-bottom: 1px solid var(--border);
                text-align: left;
              }}
              th {{
                color: var(--muted);
                text-transform: uppercase;
                letter-spacing: 0.05em;
                font-size: 11px;
              }}
              body.embed {{
                padding: 0;
                background: transparent;
              }}
              body.embed .container {{
                max-width: none;
                gap: 0;
              }}
              body.embed h1,
              body.embed .meta,
              body.embed .filters,
              body.embed .notice,
              body.embed .table-card {{
                display: none;
              }}
              body.embed .graph-card {{
                border: none;
                border-radius: 0;
                padding: 0;
                min-height: 100vh;
                height: 100vh;
                background: transparent;
              }}
              body.embed svg {{
                height: 100%;
              }}
            </style>
          </head>
          <body class="{body_class}">
            <div class="container">
              <div>
                <h1>Service Graph</h1>
                <div class="meta">Source: {escape(self.source_type or "Unknown")} | Nodes: {node_count} | Edges: {edge_count}</div>
              </div>
              <form class="filters" method="get">
                <div>
                  <label for="serviceInput">Service (comma-separated)</label>
                  <input id="serviceInput" name="service" value="{escape(service_value)}" placeholder="payment-service, api-gateway" />
                </div>
                <div>
                  <label for="directionSelect">Direction</label>
                  <select id="directionSelect" name="direction">{direction_options}</select>
                </div>
                <div>
                  <label for="depthSelect">Depth</label>
                  <select id="depthSelect" name="depth">{depth_options}</select>
                </div>
                <div>
                  <label for="layoutSelect">Layout</label>
                  <select id="layoutSelect" name="layout">{layout_options}</select>
                </div>
                <div class="actions">
                  <button type="submit">Apply</button>
                  <a href="?">Clear</a>
                </div>
              </form>
              {f'<div class="notice">{missing_note}</div>' if missing_note else ""}
              {f'<div class="notice">{limit_note}</div>' if limit_note else ""}
              <div class="graph-card">
                <svg id="graph" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">
                  <defs>
                    <marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                      <polygon points="0 0, 10 3.5, 0 7" fill="#94a3b8"></polygon>
                    </marker>
                    <marker id="arrow-highlight" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                      <polygon points="0 0, 10 3.5, 0 7" fill="#2563eb"></polygon>
                    </marker>
                  </defs>
                </svg>
              </div>
              {edge_table}
            </div>
            <script type="application/json" id="graph-data">{data_json}</script>
            <script>
              const data = JSON.parse(document.getElementById("graph-data").textContent);
              const svg = document.getElementById("graph");
              const ns = "http://www.w3.org/2000/svg";
              const nodeById = new Map(data.nodes.map((node) => [node.id, node]));
              const highlight = new Set(data.highlight || []);

              function el(name) {{
                return document.createElementNS(ns, name);
              }}

              function edgeHighlight(edge) {{
                return highlight.has(edge.source) || highlight.has(edge.target);
              }}

              data.edges.forEach((edge) => {{
                const source = nodeById.get(edge.source);
                const target = nodeById.get(edge.target);
                if (!source || !target) return;
                const line = el("line");
                line.setAttribute("x1", source.x);
                line.setAttribute("y1", source.y);
                line.setAttribute("x2", target.x);
                line.setAttribute("y2", target.y);
                const weight = edge.weight || 1;
                const thickness = 1 + (weight / (data.maxWeight || 1)) * 3;
                line.setAttribute("stroke-width", thickness.toFixed(2));
                line.setAttribute("marker-end", edgeHighlight(edge) ? "url(#arrow-highlight)" : "url(#arrow)");
                line.classList.add("edge");
                if (edgeHighlight(edge)) {{
                  line.classList.add("highlight");
                }}
                svg.appendChild(line);
              }});

              data.nodes.forEach((node) => {{
                const group = el("g");
                group.classList.add("node");
                if (highlight.has(node.id)) {{
                  group.classList.add("highlight");
                }}
                const circle = el("circle");
                const radius = 10 + Math.min(node.degree, 8);
                circle.setAttribute("cx", node.x);
                circle.setAttribute("cy", node.y);
                circle.setAttribute("r", radius);
                const title = el("title");
                title.textContent = `${{node.id}} (in: ${{node.in}}, out: ${{node.out}})`;
                circle.appendChild(title);
                const text = el("text");
                text.setAttribute("x", node.x + radius + 4);
                text.setAttribute("y", node.y + 4);
                text.textContent = node.id;
                group.appendChild(circle);
                group.appendChild(text);
                svg.appendChild(group);
              }});
            </script>
          </body>
        </html>
        """
        return HTMLResponse(content=html)
