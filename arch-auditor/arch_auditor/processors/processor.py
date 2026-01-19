from abc import ABC, abstractmethod
from typing import Any
from arch_auditor.system_state import SystemState


class Processor(ABC):
    def __init__(self, system_state: SystemState):
        self.system_state = system_state

    @abstractmethod
    def name() -> str:
        """Return the name of the processor."""
        pass

    @abstractmethod
    def requires() -> list[str]:
        """Return a list of processor names that this processor depends on."""
        pass

    @abstractmethod
    def init(self, config) -> bool:
        """Initialize the processor with the given configuration.

        Returns:
            bool: True if initialization is successful, False otherwise.
        """
        pass

    @abstractmethod
    def process(self) -> None:
        """Process the given context."""
        pass

    @abstractmethod
    def has_visualization() -> bool:
        """Check if the processor has visualization capabilities.

        Returns:
            bool: True if visualization is available, False otherwise.
        """
        pass

    @abstractmethod
    def visualize(self):
        """Visualize this processor's data."""
        pass


class ProcessorRegistry:
    def __init__(self):
        self._registry = {}

    def register(self, processor_cls: type[Processor]) -> None:
        self._registry[processor_cls.name()] = processor_cls

    def get(self, name: str) -> type[Processor] | None:
        return self._registry.get(name)


class ProcessorsBuilder:
    def __init__(
        self, processors_config, system_state, processors_registry: ProcessorRegistry
    ):
        self.config: dict[str, Any] = processors_config
        self.system_state = system_state
        self.registry = processors_registry

    def build_processors(self) -> list[Processor]:
        processors: dict[str, Processor] = {}

        def build_all(name: str) -> None:
            if name in processors:
                return  # Processor already built

            processor_cls = self.registry.get(name)
            if processor_cls is None:
                raise ValueError(f"Processor '{name}' not found in registry.")

            for require_name in processor_cls.requires():
                if require_name not in processors:
                    build_all(require_name)

            processor_instance = processor_cls(self.system_state)
            config = self.config.get(name, None)
            if not processor_instance.init(config):
                raise ValueError(f"Failed to initialize processor '{name}'.")
            processors[name] = processor_instance

        for name in self.config.keys():
            build_all(name)

        return list(processors.values())
