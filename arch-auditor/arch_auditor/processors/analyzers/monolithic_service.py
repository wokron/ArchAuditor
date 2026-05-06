from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
import statistics

class MonolithicServiceAnalyzer(Processor):
    @staticmethod
    def name() -> str:
        return "MonolithicServiceAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ServiceGraphSource", "K8sConfigSource"]

    def init(self, config) -> bool:
        self.degree_threshold = config.get("degree_threshold", 15)
        self.resource_multiplier = config.get("resource_multiplier", 5.0)
        return True

    def process(self) -> None:
        self._analyze_architecture_perspective()
        self._analyze_resource_perspective()

    def _analyze_architecture_perspective(self):
        if not hasattr(self.context.system_state, "graph"):
            return
        G = self.context.system_state.graph
        for node in G.nodes:
            #入度+出度>15-20为单体服务
            degree = G.in_degree(node) + G.out_degree(node)
            if degree > self.degree_threshold:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=f"Architecture perspective: Service '{node}' has a high degree ({degree}). "
                                f"In-degree: {G.in_degree(node)}, Out-degree: {G.out_degree(node)}. "
                                f"This exceeds the threshold of {self.degree_threshold} and indicates a potential monolithic service."
                    )
                )

    def _analyze_resource_perspective(self):
        k8s_configs = self.context.system_state.extra_attrs.get("k8s_configs", {})
        deployments = k8s_configs.get("deployments", [])
        
        cpu_requests = []
        mem_requests = []

        for dep in deployments:
            for c in dep.get("containers", []):
                reqs = c.get("resources", {}).get("requests", {})
                cpu = self._parse_k8s_cpu(reqs.get("cpu"))
                mem = self._parse_k8s_memory_mb(reqs.get("memory"))
                if cpu > 0: cpu_requests.append(cpu)
                if mem > 0: mem_requests.append(mem)
                
        if cpu_requests and mem_requests:
            median_cpu = statistics.median(cpu_requests)
            median_mem = statistics.median(mem_requests)
            
            for dep in deployments:
                name = dep.get("name", "unknown")
                for c in dep.get("containers", []):
                    c_name = c.get("name", "unknown")
                    reqs = c.get("resources", {}).get("requests", {})
                    cpu = self._parse_k8s_cpu(reqs.get("cpu"))
                    mem = self._parse_k8s_memory_mb(reqs.get("memory"))
                    
                    if median_cpu > 0 and cpu > self.resource_multiplier * median_cpu:
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.WARNING,
                                message=f"Resource perspective: Service '{name}' container '{c_name}' requests {cpu:.2f} CPU, "
                                        f"which is > {self.resource_multiplier}x the cluster median ({median_cpu:.2f} CPU)."
                            )
                        )
                    if median_mem > 0 and mem > self.resource_multiplier * median_mem:
                        self.context.reporter.report(
                            ReportMessage(
                                report_from=self.name(),
                                report_type=ReportType.WARNING,
                                message=f"Resource perspective: Service '{name}' container '{c_name}' requests {mem:.2f}MB memory, "
                                        f"which is > {self.resource_multiplier}x the cluster median ({median_mem:.2f}MB)."
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

    def _parse_k8s_memory_mb(self, mem_str):
        if not mem_str: return 0.0
        mem_str = str(mem_str)
        multiplier = 1.0
        if mem_str.endswith("Ki"): multiplier = 1 / 1024.0
        elif mem_str.endswith("Mi"): multiplier = 1.0
        elif mem_str.endswith("Gi"): multiplier = 1024.0
        elif mem_str.endswith("Ti"): multiplier = 1024.0 * 1024.0
        elif mem_str.endswith("m"): return float(mem_str[:-1]) / (1024 * 1024 * 1000)
        
        val_str = ''.join(c for c in mem_str if c.isdigit() or c == '.')
        if not val_str: return 0.0
        try:
            return float(val_str) * multiplier
        except ValueError:
            return 0.0

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
