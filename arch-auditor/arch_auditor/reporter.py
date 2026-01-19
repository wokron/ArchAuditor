from enum import Enum
from abc import ABC, abstractmethod


class ReportType(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ReportMessage:
    def __init__(self, report_from: str, report_type: ReportType, message: str):
        self.report_from = report_from
        self.report_type = report_type
        self.message = message

    def __str__(self):
        return f"[{self.report_type.value}] from {self.report_from}: {self.message}"


class Reporter(ABC):
    @abstractmethod
    def report(self, report: ReportMessage) -> None:
        pass


class ConsoleReporter(Reporter):
    def report(self, report: ReportMessage) -> None:
        print(report)
