from .processor import Processor, ProcessorRegistry, ProcessorsBuilder
from .analyzers import CircularDependencyAnalyzer
from .sources import ServicePrioritySource

native_registry = ProcessorRegistry()
#### Register native processors begin ####
native_registry.register(CircularDependencyAnalyzer)
native_registry.register(ServicePrioritySource)
#### Register native processors end ####

__all__ = [
    "Processor",
    "ProcessorRegistry",
    "ProcessorsBuilder",
    "native_registry",
    "CircularDependencyAnalyzer",
    "ServicePrioritySource",
]
