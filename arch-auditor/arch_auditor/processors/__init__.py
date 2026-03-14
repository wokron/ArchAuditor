from .processor import Processor, ProcessorRegistry, ProcessorsBuilder
from .analyzers import (
    CircularDependencyAnalyzer,
    PriorityCheckAnalyzer,
    SinglePointAnalyzer,
    K8sConfigAnalyzer,
    OverDecompositionAnalyzer,
    DockerConfigAnalyzer,
)
from .sources import (
    ServicePrioritySource,
    ServiceGraphSource,
    ServiceDependencySource,
    SingleRootDAGSource,
    K8sConfigSource,
    PrometheusMetricsSource,
    DockerConfigSource,
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
]
