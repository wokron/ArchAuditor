from .processor import Processor, ProcessorRegistry, ProcessorsBuilder
from .analyzers import (
    CircularDependencyAnalyzer,
    PriorityCheckAnalyzer,
    SinglePointAnalyzer,
    K8sConfigAnalyzer,
    OverDecompositionAnalyzer,
    DockerConfigAnalyzer,
    MonolithicServiceAnalyzer,
    ResourceUtilizationAnalyzer,
    IsolationAnalyzer,
    ConfigDriftAnalyzer,
    MaintainabilityAnalyzer,
)
from .sources import (
    ServicePrioritySource,
    ServiceGraphSource,
    ServiceDependencySource,
    SingleRootDAGSource,
    K8sConfigSource,
    PrometheusMetricsSource,
    DockerConfigSource,
    ConfigDriftSource,
    DeploymentHistorySource,
)

native_registry = ProcessorRegistry()
#### Register native processors begin ####
native_registry.register(CircularDependencyAnalyzer)
native_registry.register(ServicePrioritySource)
native_registry.register(ServiceGraphSource)
native_registry.register(ServiceDependencySource)
native_registry.register(PriorityCheckAnalyzer)
native_registry.register(SingleRootDAGSource)
native_registry.register(SinglePointAnalyzer)
native_registry.register(K8sConfigSource)
native_registry.register(K8sConfigAnalyzer)
native_registry.register(OverDecompositionAnalyzer)
native_registry.register(PrometheusMetricsSource)
native_registry.register(DockerConfigSource)
native_registry.register(DockerConfigAnalyzer)
native_registry.register(MonolithicServiceAnalyzer)
native_registry.register(ResourceUtilizationAnalyzer)
native_registry.register(IsolationAnalyzer)
native_registry.register(ConfigDriftSource)
native_registry.register(ConfigDriftAnalyzer)
native_registry.register(DeploymentHistorySource)
native_registry.register(MaintainabilityAnalyzer)
#### Register native processors end ####

__all__ = [
    "Processor",
    "ProcessorRegistry",
    "ProcessorsBuilder",
    "native_registry",
    "CircularDependencyAnalyzer",
    "ServicePrioritySource",
    "ServiceGraphSource",
    "ServiceDependencySource",
    "PriorityCheckAnalyzer",
    "SingleRootDAGSource",
    "SinglePointAnalyzer",
    "K8sConfigSource",
    "K8sConfigAnalyzer",
    "OverDecompositionAnalyzer",
    "PrometheusMetricsSource",
    "DockerConfigSource",
    "DockerConfigAnalyzer",
    "MonolithicServiceAnalyzer",
    "ResourceUtilizationAnalyzer",
    "IsolationAnalyzer",
    "ConfigDriftSource",
    "ConfigDriftAnalyzer",
    "DeploymentHistorySource",
    "MaintainabilityAnalyzer",
]
