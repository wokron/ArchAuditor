from arch_auditor.system_state import SystemState


class AuditContext:
    def __init__(self, system_state: SystemState):
        self.system_state = system_state
