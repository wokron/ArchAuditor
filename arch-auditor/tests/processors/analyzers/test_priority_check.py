from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter
from arch_auditor.priority_manager import InMemoryPriorityManager


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_priority_check_analyze_check_strong_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                    ("ServiceC", "ServiceD"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "ServiceA", "to": "ServiceB", "type": "strong"},
                    {"from": "ServiceB", "to": "ServiceC", "type": "strong"},
                    {"from": "ServiceC", "to": "ServiceD", "type": "strong"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceA", 3)
    auditor.priority_manager.set_priority("ServiceB", 1)
    auditor.priority_manager.set_priority("ServiceC", 2)
    auditor.priority_manager.set_priority("ServiceD", 0)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 1  # B -> C is a violation
    assert any(
        "Priority violation: 'ServiceB' (priority: 1) depends strongly on 'ServiceC' (priority: 2)."
        in msg
        for msg in messages
    )


def test_priority_check_analyze_check_weak_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceX", "ServiceY"),
                    ("ServiceZ", "ServiceY"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {},
            },
            "ServiceDependencySource": {
                "type": "Mock",
                "dependencies": [
                    {"from": "ServiceX", "to": "ServiceY", "type": "weak"},
                    {"from": "ServiceZ", "to": "ServiceY", "type": "weak"},
                ],
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceX", 1)
    auditor.priority_manager.set_priority("ServiceY", 2)
    auditor.priority_manager.set_priority("ServiceZ", 3)

    auditor.invoke()
    messages = reporter.messages
    assert len(messages) == 1  # Suggestion for ServiceY
    assert any(
        "Consider reviewing 'ServiceY' (priority: 2) as it is only weakly depended upon by services ['ServiceX', 'ServiceZ']."
        in msg
        for msg in messages
    )


def test_priority_check_analyze_with_inferred_jaeger_dependencies():
    config = {
        "processors": {
            "ServiceGraphSource": {
                "type": "Mock",
                "edges": [
                    ("ServiceA", "ServiceB"),
                    ("ServiceB", "ServiceC"),
                ],
            },
            "ServicePrioritySource": {
                "type": "InMemory",
            },
            "PrometheusMetricsSource": {
                "type": "Mock",
                "metrics": {
                    "latency": {
                        "ServiceA": [(1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0)],
                        "ServiceB": [(1, 2.0), (2, 4.0), (3, 6.0), (4, 8.0)],
                        "ServiceC": [(1, 3.0), (2, 6.0), (3, 9.0), (4, 12.0)],
                    },
                    "error_rate": {
                        "ServiceA": [(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4)],
                        "ServiceB": [(1, 0.2), (2, 0.4), (3, 0.6), (4, 0.8)],
                        "ServiceC": [(1, 0.3), (2, 0.6), (3, 0.9), (4, 1.2)],
                    },
                },
            },
            "ServiceDependencySource": {
                "type": "Jaeger",
                "jaeger_url": "http://jaeger.test",
                "strong_call_threshold": 100,
                "correlation_threshold": 0.7,
                "lookback_ms": 60000,
            },
            "PriorityCheckAnalyzer": {},
        }
    }

    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)

    auditor.priority_manager.set_priority("ServiceA", 3)
    auditor.priority_manager.set_priority("ServiceB", 1)
    auditor.priority_manager.set_priority("ServiceC", 2)

    dependency_source = next(
        processor
        for processor in auditor.processors
        if processor.name() == "ServiceDependencySource"
    )

    def fake_process_jaeger():
        G = dependency_source.context.system_state.graph
        metrics = dependency_source.context.system_state.extra_attrs["metrics_timeseries"]
        latency = metrics["latency"]
        error_rate = metrics["error_rate"]
        jaeger_payload = [
            ("ServiceA", "ServiceB", 150),
            ("ServiceB", "ServiceC", 150),
        ]
        for parent, child, call_count in jaeger_payload:
            corr = dependency_source._dependency_correlation(
                latency.get(parent, []),
                latency.get(child, []),
                error_rate.get(parent, []),
                error_rate.get(child, []),
            )
            dep_type = (
                "strong"
                if call_count >= dependency_source.strong_threshold
                and corr >= dependency_source.correlation_threshold
                else "weak"
            )
            G.edges[parent, child]["dependency_type"] = dep_type

    dependency_source._process_jaeger = fake_process_jaeger

    auditor.invoke()
    messages = reporter.messages
    assert any(
        "Priority violation: 'ServiceB' (priority: 1) depends strongly on 'ServiceC' (priority: 2)."
        in msg
        for msg in messages
    )
