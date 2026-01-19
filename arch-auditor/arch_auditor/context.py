from arch_auditor.system_state import SystemState
from arch_auditor.reporter import Reporter


class AuditContext:
    def __init__(self, system_state: SystemState, reporter: Reporter):
        self.system_state = system_state
        self.reporter = reporter
