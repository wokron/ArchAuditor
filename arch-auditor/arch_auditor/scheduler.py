from arch_auditor.processors import Processor


class Scheduler:
    def __init__(self, processor_list: list[Processor]):
        self.processor_list = processor_list
        self.order = self.__plan()

    def process(self):
        for processor in self.order:
            processor.process()

    def __plan(self):
        processors = {p.name(): p for p in self.processor_list}
        order: list[Processor] = []
        visited = set()

        def dfs(processor: Processor):
            if processor in visited:
                return
            visited.add(processor)
            for dep_name in processor.requires():
                dfs(processors[dep_name])
            order.append(processor)

        for processor in self.processor_list:
            dfs(processor)

        assert len(order) == len(self.processor_list)
        return order
