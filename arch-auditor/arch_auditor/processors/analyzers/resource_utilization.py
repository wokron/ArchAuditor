from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
import statistics

class ResourceUtilizationAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "ResourceUtilizationAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["K8sConfigSource", "ServicePrioritySource", "PrometheusMetricsSource"]

    def init(self, config) -> bool:
        self.waste_threshold = config.get("waste_threshold", 0.3)
        self.limit_risk_threshold = config.get("limit_risk_threshold", 0.7)
        return True

    def process(self) -> None:
        G = None
        if hasattr(self.context.system_state, "graph"):
            G = self.context.system_state.graph
            
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        deployments = k8s_configs.get("deployments", [])
        cpu_usage_data = self.context.system_state.extra_attrs.get("metrics_timeseries", {}).get("cpu_usage", {})
        
        for dep in deployments:
            name = dep.get("name", "unknown")
            priority = -1 # 默认非高优先级
            
            if G and name in G.nodes:
                priority = G.nodes[name].get("priority", -1)
                
            for c in dep.get("containers", []):
                c_name = c.get("name", "unknown")
                reqs = c.get("resources", {}).get("requests", {})
                limits = c.get("resources", {}).get("limits", {})
                
                req_cpu = self._parse_k8s_cpu(reqs.get("cpu"))
                limit_cpu = self._parse_k8s_cpu(limits.get("cpu"))
                
                #priority0-1的属于高优先级
                if priority != -1 and priority <= 1: 
                    if "cpu" not in reqs or req_cpu <= 0:
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.ERROR,
                                message=f"Service '{name}' is a high-priority service (priority {priority}) but has no CPU request configured. This is unreasonable.",
                            )
                        )

                #计算实际利用率
                actual_cpu_timeseries = cpu_usage_data.get(name, [])
                if actual_cpu_timeseries:
                    values = [val for ts, val in actual_cpu_timeseries if val is not None]
                    if values:
                        avg_utilization = statistics.mean(values)
                        
                        #平均实际资源利用率<0.3*request时说明资源浪费
                        if req_cpu > 0 and avg_utilization < self.waste_threshold * req_cpu:
                            self.context.reporter.report(
                                ReportMessage(
                                    report_from=self.name(),
                                    report_type=ReportType.WARNING,
                                    message=f"Service '{name}' (container '{c_name}') is wasting CPU. "
                                            f"Actual average utilization ({avg_utilization:.3f}) < {self.waste_threshold * 100}% of request ({req_cpu})."
                                )
                            )
                            
                       
                        short_term_values = values[-5:] if len(values) >= 5 else values
                        short_term_avg_utilization = statistics.mean(short_term_values)
                        
                        #服务的短期平均实际利用率>0.7*limit时，说明资源接近极限，稳定性变差
                        if limit_cpu > 0 and short_term_avg_utilization > self.limit_risk_threshold * limit_cpu:
                            self.context.reporter.report(
                                ReportMessage(
                                    report_from=self.name(),
                                    report_type=ReportType.WARNING,
                                    message=f"Service '{name}' (container '{c_name}') stability risk. "
                                            f"Short-term average CPU utilization ({short_term_avg_utilization:.3f}) > {self.limit_risk_threshold * 100}% of limit ({limit_cpu})."
                                )
                            )

    def _parse_k8s_cpu(self, cpu_str):
        if not cpu_str: return 0.0
        cpu_str = str(cpu_str)
        if cpu_str.endswith("m"): return float(cpu_str[:-1]) / 1000.0
        try:
            return float(cpu_str)
        except ValueError:
            return 0.0

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
