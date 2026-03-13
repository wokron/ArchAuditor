import re
for i, line in enumerate(open('e:/Arch-project/arch-auditor/arch_auditor/processors/sources/service_graph.py')):
    if 'def _process_jaeger' in line: print(i)