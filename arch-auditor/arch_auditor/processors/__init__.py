from .processor import Processor, ProcessorRegistry, ProcessorsBuilder
from .analyzers import (
    CircularDependencyAnalyzer,
    PriorityCheckAnalyzer,
    SinglePointAnalyzer,
)
from .sources import (
    ServicePrioritySource,
    ServiceGraphSource,
    ServiceDependencySource,
    SingleRootDAGSource,
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
]
