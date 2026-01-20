from fastapi import APIRouter, FastAPI
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
        priority_manager: PriorityManager,
        registry: ProcessorRegistry | None = None,
    ):
        self.priority_manager = priority_manager
        self.config = config
        self.config.setdefault("processors", {})[
            "ServicePrioritySource"
        ] = self.priority_manager

        self.time_scheduler = AsyncIOScheduler()

        self.reporter = ServiceReporter(self)
        self.arch_auditor = ArchAuditor(
            config=self.config, registry=registry, reporter=self.reporter
        )

        self.app = FastAPI(lifespan=self._generate_time_scheduler_lifespan())
        self._setup_routes()

    def _setup_routes(self):
        self._setup_api_routes()
        self._setup_web_routes()

    def _setup_api_routes(self):
        api = APIRouter()

        api.post("/audit")(self.trigger_audit)
        api.get("/priorities")(self.list_priorities)
        api.get("/priorities/{service}")(self.get_priority)
        api.post("/priorities/{service}/{priority}")(self.post_priority)
        api.get("/reports")(self.list_reports)

        self.app.include_router(api, prefix="/api")

    def _setup_web_routes(self):
        processors_with_vis: list[Processor] = []
        for processor in self.arch_auditor.processors:
            if processor.has_visualization():
                processors_with_vis.append(processor)

        vis = APIRouter()

        def list_processors():
            return [processor.name() for processor in processors_with_vis]

        vis.get("/")(list_processors)

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

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        import uvicorn

        uvicorn.run(self.app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    import yaml
    from arch_auditor.priority_manager import InMemoryPriorityManager

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

    priority_manager = InMemoryPriorityManager()
    service = ArchAuditService(config=config, priority_manager=priority_manager)
    service.run(host=args.host, port=args.port)
