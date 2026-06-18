import datetime
from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType


class ConfigDriftAnalyzer(Processor):
    """Detect manual configuration changes that have persisted too long.

    A "manual" change is one where changed_by is not a recognised controller.
    If a resource has both manual and controller events, the summary prefers the
    latest manual event so a user-triggered change is not visually hidden by a
    later controller reconciliation. If the selected manual change stays
    unreverted beyond *drift_threshold_hours* a WARNING is raised.
    """

    @staticmethod
    def name() -> str:
        return "ConfigDriftAnalyzer"

    @staticmethod
    def requires() -> list[str]:
        return ["ConfigDriftSource"]

    def init(self, config) -> bool:
        self.drift_threshold_hours = config.get("drift_threshold_hours", 24)
        return True

    def process(self) -> None:
        events = self.context.system_state.extra_attrs.get("config_drift_events", [])
        now = datetime.datetime.now(datetime.timezone.utc)
        controller_kinds = {"controller", "operator", "system", "gitops"}
        summary = []

        if not events:
            self.context.system_state.extra_attrs["config_drift_summary"] = summary
            return

        events_by_resource: dict[str, list[dict]] = {}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            resource = ev.get("resource") or "unknown"
            events_by_resource.setdefault(resource, []).append(ev)

        for resource, resource_events in sorted(events_by_resource.items()):
            parsed_events = []
            for ev in resource_events:
                changed_at_str = ev.get("changed_at")
                if not changed_at_str:
                    continue
                changed_at = _parse_timestamp_utc(changed_at_str)
                if changed_at is None:
                    continue
                parsed_events.append((changed_at, ev))

            if not parsed_events:
                continue

            parsed_events.sort(key=lambda item: item[0])
            latest_observed_at, latest_observed_event = parsed_events[-1]
            manual_events = [
                (changed_at, ev)
                for changed_at, ev in parsed_events
                if (ev.get("changed_by") or "").lower() not in controller_kinds
            ]
            if manual_events:
                selected_changed_at, selected_event = manual_events[-1]
                selected_event_strategy = "latest_manual_change"
            else:
                selected_changed_at, selected_event = latest_observed_at, latest_observed_event
                selected_event_strategy = "latest_observed_change"

            changed_by = (selected_event.get("changed_by") or "").lower()
            delta = now - selected_changed_at
            age_hours = max(delta.total_seconds() / 3600.0, 0.0)
            is_manual = changed_by not in controller_kinds
            is_drifted = is_manual and delta > datetime.timedelta(
                hours=self.drift_threshold_hours
            )

            summary_item = {
                "resource": resource,
                "namespace": selected_event.get("namespace"),
                "kind": selected_event.get("kind"),
                "name": selected_event.get("name"),
                "revision": selected_event.get("revision"),
                "latest_changed_at": selected_changed_at.isoformat(),
                "latest_changed_by": selected_event.get("changed_by"),
                "manager": selected_event.get("manager"),
                "reason": selected_event.get("reason"),
                "change_cause": selected_event.get("change_cause"),
                "event_count": len(parsed_events),
                "age_hours": round(age_hours, 2),
                "drift_threshold_hours": self.drift_threshold_hours,
                "is_manual_change": is_manual,
                "is_drifted": is_drifted,
                "verb": selected_event.get("verb"),
                "username": selected_event.get("username"),
                "user_agent": selected_event.get("user_agent"),
                "source_ip": selected_event.get("source_ip"),
                "event_source": selected_event.get("event_source"),
                "selected_event_strategy": selected_event_strategy,
                "latest_observed_at": latest_observed_at.isoformat(),
                "latest_observed_by": latest_observed_event.get("changed_by"),
                "latest_observed_manager": latest_observed_event.get("manager"),
                "latest_observed_reason": latest_observed_event.get("reason"),
                "latest_observed_verb": latest_observed_event.get("verb"),
            }
            summary.append(summary_item)

            if is_drifted:
                self.context.reporter.report(
                    ReportMessage(
                        report_from=self.name(),
                        report_type=ReportType.WARNING,
                        message=(
                            f"Configuration drift detected for '{resource}': "
                            f"manual change by '{selected_event.get('changed_by')}' has persisted "
                            f"for {delta.days}d {delta.seconds // 3600}h "
                            f"(threshold: {self.drift_threshold_hours}h)."
                        ),
                    )
                )

        self.context.system_state.extra_attrs["config_drift_summary"] = summary

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass


def _parse_timestamp_utc(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)
