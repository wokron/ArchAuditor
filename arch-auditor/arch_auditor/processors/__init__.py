from .processor import Processor, ProcessorRegistry, ProcessorsBuilder
from .analyzers import CircularDependencyAnalyzer

native_registry = ProcessorRegistry()
#### Register native processors begin ####
native_registry.register(CircularDependencyAnalyzer)
#### Register native processors end ####

__all__ = [
    "Processor",
    "ProcessorRegistry",
    "ProcessorsBuilder",
    "native_registry",
    "CircularDependencyAnalyzer",
]
