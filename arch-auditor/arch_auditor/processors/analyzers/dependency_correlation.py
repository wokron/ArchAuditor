from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class DependencyCorrelationAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "DependencyCorrelationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceDependencySource"]

    def init(self, config) -> bool:
        config = config or {}
        self.warning_correlation_threshold = float(
            config.get("warning_correlation_threshold", 0.7)
        )
        self.error_correlation_threshold = float(
            config.get("error_correlation_threshold", 0.9)
        )
        return True

    def process(self) -> None:
        graph = self.context.system_state.graph
        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            return

        for source, target, edge_data in graph.edges(data=True):
            edge_data = edge_data or {}
            correlation_status = edge_data.get("dependency_correlation_status")
            correlation = edge_data.get("dependency_correlation")
            if correlation_status != "ok" or correlation is None:
                continue

            if correlation < self.warning_correlation_threshold:
                continue

            report_type = (
                ReportType.ERROR
                if correlation >= self.error_correlation_threshold
                else ReportType.WARNING
            )
            call_count = edge_data.get("call_count")
            call_count_suffix = (
                f", call_count: {call_count}" if call_count is not None else ""
            )
            self.context.reporter.report(
                ReportMessage(
                    report_from=self.name(),
                    report_type=report_type,
                    message=(
                        "Strong dependency detected from Pearson correlation: "
                        f"'{source}' -> '{target}' has correlation "
                        f"{correlation:.4f}{call_count_suffix}."
                    ),
                )
            )

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
