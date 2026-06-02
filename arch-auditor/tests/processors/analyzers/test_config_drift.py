from arch_auditor.arch_auditor import ArchAuditor
from arch_auditor.reporter import ReportMessage, Reporter


class MockReporter(Reporter):
    def __init__(self):
        self.messages = []

    def report(self, report: ReportMessage) -> None:
        self.messages.append(str(report))


def test_manual_change_drift():
    """Manual change older than 24h → WARNING."""
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": "2026-05-20T10:00:00+00:00",
                        "changed_by": "user",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert any("Configuration drift detected" in msg for msg in reporter.messages)
    assert any("my-svc" in msg for msg in reporter.messages)


def test_controller_change_ignored():
    """Controller-triggered change → no alert."""
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": "2026-05-20T10:00:00+00:00",
                        "changed_by": "controller",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert len(reporter.messages) == 0


def test_recent_change_ignored():
    """Change within threshold → no alert."""
    import datetime
    recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    config = {
        "processors": {
            "ConfigDriftSource": {
                "type": "Mock",
                "events": [
                    {
                        "resource": "deployment/default/my-svc",
                        "changed_at": recent.isoformat(),
                        "changed_by": "user",
                    },
                ],
            },
            "ConfigDriftAnalyzer": {
                "drift_threshold_hours": 24,
            },
        }
    }
    reporter = MockReporter()
    auditor = ArchAuditor(config, reporter=reporter)
    auditor.invoke()
    assert not any("drift" in msg for msg in reporter.messages)
