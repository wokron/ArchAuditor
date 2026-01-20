from abc import ABC, abstractmethod


class PriorityManager(ABC):
    @abstractmethod
    def set_priority(self, service, priority):
        pass

    @abstractmethod
    def get_priority(self, service):
        pass

    @abstractmethod
    def remove_priority(self, item):
        pass

    @abstractmethod
    def list_priorities(self):
        pass


class InMemoryPriorityManager(PriorityManager):
    def __init__(self):
        self.priorities = {}

    def set_priority(self, service, priority):
        self.priorities[service] = priority

    def get_priority(self, service):
        return self.priorities.get(service, None)

    def remove_priority(self, item):
        if item in self.priorities:
            del self.priorities[item]

    def list_priorities(self):
        return self.priorities.items()
