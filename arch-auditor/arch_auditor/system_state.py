from typing import Any
import networkx as nx


class SystemState:
    def __init__(self):
        self.graph = nx.DiGraph()
        self.extra_attrs: dict[str, Any] = {}