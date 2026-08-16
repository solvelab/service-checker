import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus


class GcpStatusMonitor:
    def __init__(self, slug: str = "gcp") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("gcp monitor not configured")

        start = time.perf_counter()
        try:
            response = await http_client.get(
                self.config.url,
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": self.config.user_agent},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="gcp status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000
        rule_status, rule_reason, payload, reason_items = self._evaluate_rule(data)

        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="gcp rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        if rule_status == MonitorStatus.ALERT:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="gcp status degraded",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="gcp status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _evaluate_rule(
        self, data: object
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None, None

        rule_kind = self.config.rule.kind
        rule_value = self.config.rule.value

        if rule_kind == "status":
            return self._evaluate_status_rule(data, rule_value)

        text_body = json.dumps(data)
        if not rule_value:
            return MonitorStatus.OK, None, None, None

        if rule_kind == "keyword":
            if rule_value.lower() in text_body.lower():
                return MonitorStatus.ALERT, f"keyword '{rule_value}' detected", None, None
            return MonitorStatus.OK, None, None, None

        if rule_kind == "regex":
            try:
                pattern = re.compile(rule_value, re.IGNORECASE)
            except re.error as exc:
                return MonitorStatus.ERROR, f"invalid regex: {exc}", None, None
            if pattern.search(text_body) is not None:
                return MonitorStatus.ALERT, f"regex '{rule_value}' matched", None, None
            return MonitorStatus.OK, None, None, None

        return MonitorStatus.ERROR, f"unsupported rule kind '{rule_kind}'", None, None

    def _evaluate_status_rule(
        self, data: object, rule_value: str
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        if not isinstance(data, list):
            return MonitorStatus.ERROR, "unexpected incidents payload", None, None

        statuses = {item.strip().lower() for item in (rule_value or "").split(",") if item.strip()}
        if not statuses:
            statuses = {"service_disruption", "service_outage", "service_information"}

        targets = {item.strip().lower() for item in self.config.service_filter}
        active_incidents = []
        for incident in data:
            if not isinstance(incident, dict):
                continue

            status_impact = (incident.get("status_impact") or "").lower()
            end_time = incident.get("end")
            locations = (
                incident.get("currently_affected_locations")
                or incident.get("affected_locations")
                or []
            )

            if not locations:
                continue

            # Only consider incidents that are not ended yet.
            if end_time:
                continue

            if status_impact and status_impact not in statuses:
                continue

            matched_locations = [
                loc
                for loc in locations
                if _matches_location(loc, targets)
            ]
            if not matched_locations:
                continue

            active_incidents.append(
                {
                    "id": incident.get("id"),
                    "status": status_impact or incident.get("most_recent_update", {}).get("status", ""),
                    "regions": [loc.get("id") or loc.get("title") for loc in matched_locations],
                    "most_recent_update": incident.get("most_recent_update"),
                }
            )

        if active_incidents:
            # `regions` is a list: interpolating it directly leaked a Python repr
            # (`['us-central1', 'us-east1']: service_outage`) straight into the alert.
            items = [
                f"{', '.join(str(r) for r in inc['regions'])}: {inc['status'] or 'unknown'}"
                for inc in active_incidents
            ]
            return MonitorStatus.ALERT, "; ".join(items), active_incidents, items

        return MonitorStatus.OK, None, [], None


def _matches_location(location: Dict, targets: set[str]) -> bool:
    if not targets:
        return True
    loc_id = (location.get("id") or "").lower()
    title = (location.get("title") or "").lower()
    return any(target in {loc_id, title} for target in targets)


def get_monitor(slug: str = "gcp") -> GcpStatusMonitor:
    return GcpStatusMonitor(slug=slug)
