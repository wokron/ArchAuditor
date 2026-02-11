from pathlib import Path
from datetime import datetime, timezone

import networkx as nx

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from arch_auditor.priority_manager import PriorityManager
from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.processors import ProcessorRegistry
from arch_auditor.reporter import Reporter, ReportMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from arch_auditor.processors import Processor


class ServiceReporter(Reporter):
    def __init__(self, service: "ArchAuditService"):
        self.service = service
        self.messages: list[ReportMessage] = []

    def report(self, msg: ReportMessage):
        self.messages.append(msg)

    def list_messages(self) -> list[ReportMessage]:
        return self.messages


class ArchAuditService:
    def __init__(
        self,
        config: dict,
        registry: ProcessorRegistry | None = None,
    ):
        self.priority_manager = None
        self.config = config

        self.time_scheduler = AsyncIOScheduler()

        self.reporter = ServiceReporter(self)
        self.arch_auditor = ArchAuditor(
            config=self.config, registry=registry, reporter=self.reporter
        )

        self.priority_manager = self.arch_auditor.priority_manager

        self.app = FastAPI(lifespan=self._generate_time_scheduler_lifespan())

        # Resolve templates relative to this module so the app can be started
        # from any working directory.
        templates_dir = Path(__file__).resolve().parent / "templates"
        self.templates = Jinja2Templates(directory=str(templates_dir))

        self._setup_routes()

    def _setup_routes(self):
        self._setup_api_routes()
        self._setup_web_routes()

    def _setup_api_routes(self):
        api = APIRouter()

        api.post("/audit")(self.trigger_audit)
        api.get("/reports")(self.list_reports)
        api.get("/service-graph")(self.get_service_graph)

        if self.priority_manager is not None:
            api.get("/priorities")(self.list_priorities)
            api.get("/priorities/{service}")(self.get_priority)
            api.post("/priorities/{service}/{priority}")(self.post_priority)

        self.app.include_router(api, prefix="/api")

    def _setup_web_routes(self):
        # Control panel (dashboard)
        @self.app.get("/", response_class=HTMLResponse)
        def dashboard(request: Request):
            return self.templates.TemplateResponse(
                "dashboard.html",
                {
                    "request": request,
                    "priority_enabled": self.priority_manager is not None,
                },
            )

        @self.app.get("/dashboard", response_class=HTMLResponse)
        def dashboard_alias(request: Request):
            return dashboard(request)

        @self.app.get("/ServiceAnalysisVisualization", response_class=HTMLResponse)
        def service_analysis_visualization(request: Request):
            return self.templates.TemplateResponse(
                "Service_analysis.html",
                {
                    "request": request,
                },
            )

        @self.app.get("/ControlCenterVisualization", response_class=HTMLResponse)
        def control_center_visualization(request: Request):
            return self.templates.TemplateResponse(
                "control_center.html",
                {
                    "request": request,
                },
            )

        @self.app.get("/ResourceAuditVisualization", response_class=HTMLResponse)
        def resource_audit_visualization(request: Request):
            return self.templates.TemplateResponse(
                "Resource_audit.html",
                {
                    "request": request,
                },
            )

        processors_with_vis: list[Processor] = []
        for processor in self.arch_auditor.processors:
            if processor.has_visualization():
                processors_with_vis.append(processor)

        vis = APIRouter()

        def list_processors_with_vis():
            return [processor.name() for processor in processors_with_vis]

        vis.get("/")(list_processors_with_vis)

        for processor in processors_with_vis:
            vis.get(f"/{processor.name()}")(processor.visualize)

        self.app.include_router(vis, prefix="/vis")

    def _generate_time_scheduler_lifespan(self):
        async def lifespan(app: FastAPI):
            self._setup_time_scheduled_tasks()
            self.time_scheduler.start()
            yield
            self.time_scheduler.shutdown()

        return lifespan

    def _setup_time_scheduled_tasks(self):
        cron_config = self.config.get("scheduler", {}).get("cron", None)
        if cron_config:
            trigger = CronTrigger.from_crontab(cron_config)
            self.time_scheduler.add_job(
                self.trigger_audit,
                trigger=trigger,
                id="scheduled_audit_job",
                replace_existing=True,
            )

    def trigger_audit(self):
        self.arch_auditor.invoke()

    def list_priorities(self):
        return dict(self.priority_manager.list_priorities())

    def get_priority(self, service):
        return self.priority_manager.get_priority(service)

    def post_priority(self, service: str, priority: int):
        self.priority_manager.set_priority(service, priority)
        return {"service": service, "priority": priority}

    def list_reports(self):
        return [str(msg) for msg in self.reporter.list_messages()]

    def get_service_graph(self, view: str | None = None):
        graph = self.arch_auditor.system_state.graph
        view_name = (view or "hierarchy").lower()
        working_graph = graph
        message = None

        if view_name == "circular":
            cycles = [cycle for cycle in nx.simple_cycles(graph) if len(cycle) > 1]
            cycle_nodes = set()
            for cycle in cycles:
                cycle_nodes.update(cycle)
            if cycle_nodes:
                working_graph = graph.subgraph(cycle_nodes).copy()
            else:
                working_graph = nx.DiGraph()
                message = "No circular dependencies detected."
        elif view_name == "dominator":
            roots = [node for node in graph.nodes if graph.in_degree(node) == 0]
            if len(roots) == 1:
                try:
                    dominators = nx.immediate_dominators(graph, start=roots[0])
                    dom_graph = nx.DiGraph()
                    for node in graph.nodes:
                        dom_graph.add_node(node, **(graph.nodes[node] or {}))
                    for node, dom_node in dominators.items():
                        if node == dom_node:
                            continue
                        dom_graph.add_edge(dom_node, node)
                    working_graph = dom_graph
                except Exception:
                    working_graph = graph
                    message = "Failed to compute dominator tree."
            else:
                working_graph = graph
                message = "Dominator view requires a single root."
        nodes = []
        priority_lookup = {}
        if self.priority_manager is not None:
            try:
                priority_lookup = dict(self.priority_manager.list_priorities())
            except Exception:
                priority_lookup = {}

        for node in working_graph.nodes:
            attrs = graph.nodes[node] or {}
            priority = attrs.get("priority")
            if priority is None:
                priority = priority_lookup.get(node)
            try:
                priority_value = int(priority) if priority is not None else None
            except (TypeError, ValueError):
                priority_value = None

            status = "healthy"
            if priority_value == 0:
                status = "danger"
            elif priority_value == 1:
                status = "warning"

            service_level = "standard"
            if priority_value == 0:
                service_level = "critical"
            elif priority_value == 1:
                service_level = "high"
            elif priority_value == 2:
                service_level = "standard"
            elif priority_value == 3:
                service_level = "low"

            nodes.append(
                {
                    "id": str(node),
                    "priority": priority_value,
                    "status": status,
                    "service_level": service_level,
                    "in_degree": int(graph.in_degree(node)),
                    "out_degree": int(graph.out_degree(node)),
                }
            )

        edges = []
        for source, target, attrs in working_graph.edges(data=True):
            weight = None
            if attrs:
                weight = (
                    attrs.get("call_count")
                    or attrs.get("callCount")
                    or attrs.get("weight")
                )
            edges.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "weight": weight,
                }
            )

        return {
            "nodes": nodes,
            "edges": edges,
            "view": view_name,
            "message": message,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        import uvicorn

        uvicorn.run(self.app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Arch Audit Service")
    parser.add_argument(
        "--config", type=str, required=True, help="Path to the configuration YAML file"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to run the service on"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to run the service on"
    )
    args = parser.parse_args()

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)
    if config is None:
        config = {}

    service = ArchAuditService(config=config)
    service.run(host=args.host, port=args.port)
