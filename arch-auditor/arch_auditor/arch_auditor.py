from arch_auditor.system_state import SystemState
from arch_auditor.processors.processor import ProcessorRegistry, ProcessorsBuilder
from arch_auditor.processors import native_registry
from arch_auditor.reporter import ConsoleReporter, Reporter
from arch_auditor.scheduler import Scheduler
from arch_auditor.context import AuditContext


class ArchAuditor:
    def __init__(
        self,
        config,
        registry: ProcessorRegistry | None = None,
        reporter: Reporter | None = None,
    ):
        self.config = config
        self.system_state = SystemState()

        if registry is None:
            self.registry = native_registry
        else:
            self.registry = ProcessorRegistry()
            self.registry.join(native_registry)
            self.registry.join(registry)

        if reporter is None:
            self.reporter = ConsoleReporter()
        else:
            self.reporter = reporter

        self.context = AuditContext(self.system_state, self.reporter)

        self.processors = ProcessorsBuilder(
            self.config.get("processors", {}),
            self.context,
            self.registry,
        ).build_processors()

        self.scheduler = Scheduler(self.processors)

    def invoke(self) -> None:
        self.scheduler.process()
