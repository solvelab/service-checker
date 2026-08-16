"""Alertmanager channel.

Alertmanager expects clients to retransmit firing alerts continuously: with `endsAt`
omitted it sets `now + resolve_timeout` (5 minutes by default) and resolves the alert
on its own once that elapses. The Service Checker does the opposite — the state machine
suppresses repeats inside `NOTIFICATION_REPEAT_MINUTES`, and it suppresses them *before*
any channel is called, so a channel cannot opt out of the throttle.

Left alone, those two produce flapping: the alert self-resolves at 5 minutes, is recreated
at 10, and alternates forever.

The way out is an explicit `endsAt` far enough ahead that the alert cannot expire between
two sends. The existing repeat window then *is* the heartbeat Alertmanager asks for, and
no change to the state machine is needed. Recovery sends `endsAt` in the past so the alert
resolves immediately instead of waiting the margin out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from ...core.types import MonitorResult

_ALERTS_PATH = "/api/v2/alerts"

# `alertname` is the rule name in Prometheus convention; the labels below identify the
# instance. A varying alertname would create a fresh alert every cycle instead of
# deduplicating, and would break grouping and silencing on the Alertmanager side.
ALERTNAME_SERVICE = "ServiceCheckerIncident"
ALERTNAME_MONITORING = "ServiceCheckerMonitoringFailure"

# Static configuration must never redefine what identifies an alert.
RESERVED_LABELS = frozenset(
    {"alertname", "module", "check_id", "component", "event", "source", "severity"}
)

_FLOOR_SECONDS = 300.0


class AlertmanagerNotifier:
    def __init__(self, config) -> None:
        self.config = config

    async def send_alert(self, **kwargs) -> None:
        await self._send(ALERTNAME_SERVICE, firing=True, **kwargs)

    async def send_recovery(self, **kwargs) -> None:
        await self._send(ALERTNAME_SERVICE, firing=False, **kwargs)

    async def send_monitor_error(self, **kwargs) -> None:
        await self._send(ALERTNAME_MONITORING, firing=True, **kwargs)

    async def send_monitor_recovered(self, **kwargs) -> None:
        await self._send(ALERTNAME_MONITORING, firing=False, **kwargs)

    # ------------------------------------------------------------------

    async def _send(
        self,
        alertname: str,
        *,
        firing: bool,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
        **_ignored,
    ) -> None:
        if not self.config.url:
            logger.warning(
                "alertmanager notifier missing url; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "alertmanager",
                },
            )
            return

        alert = self._build_alert(
            alertname, firing, module_id, result, interval_seconds, event_name, event_time
        )
        url = self.config.url.rstrip("/") + _ALERTS_PATH
        headers = {}
        if self.config.token:
            headers[self.config.header_name] = self.config.token

        try:
            response = await http_client.post(
                url, json=[alert], headers=headers, timeout=10.0
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "alertmanager notification failed",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "alertmanager",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        status = getattr(response, "status_code", 0)
        if status >= 400:
            logger.error(
                "alertmanager notification rejected",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "alertmanager",
                    "reason": f"status {status}: {_body_text(response)}",
                },
            )
            return

        logger.info(
            "alertmanager notification sent",
            extra={
                "event": "notify",
                "module_id": module_id,
                "target": "alertmanager",
                "check_id": alert["labels"]["check_id"],
            },
        )

    def _build_alert(
        self,
        alertname: str,
        firing: bool,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        event_name: str,
        event_time: datetime,
    ) -> Dict[str, Any]:
        component = _component_of(result)
        check_id = f"{module_id}:{component}" if component else module_id

        labels: Dict[str, str] = {
            "alertname": alertname,
            "source": "service-checker",
            "module": module_id,
            "check_id": check_id,
            "severity": "critical" if alertname == ALERTNAME_MONITORING else "warning",
        }
        if component:
            labels["component"] = component

        # Static labels are merged first so the computed identity always wins.
        for key, value in (self.config.extra_labels or {}).items():
            if key not in RESERVED_LABELS:
                labels[key] = str(value)

        annotations: Dict[str, str] = {}
        # Free text lives here, never in a label: a distinct reason per incident would
        # explode label cardinality on the Alertmanager side.
        if result.reason:
            annotations["summary"] = result.reason
        if result.message:
            annotations["description"] = result.message
        if result.reason_items:
            annotations["incidents"] = "\n".join(result.reason_items)
        annotations["event"] = event_name

        ends_at = event_time + timedelta(
            seconds=self._resolve_after(interval_seconds)
            if firing
            else -1.0
        )

        return {
            "labels": labels,
            "annotations": annotations,
            "startsAt": _rfc3339(event_time),
            "endsAt": _rfc3339(ends_at),
        }

    def _resolve_after(self, interval_seconds: int) -> float:
        """How far ahead `endsAt` sits on a firing alert.

        It must exceed the real gap between two sends, which is the repeat window plus
        up to one check interval — the resend lands on the first cycle at or after the
        window elapses. Double both for slack.

        The cost of a generous margin: if the Service Checker dies, its alerts stay
        firing until this elapses instead of resolving by Alertmanager's own timeout.
        That is the intended trade — a dead checker should not silently clear alerts.
        """
        configured = getattr(self.config, "resolve_after_seconds", 0.0) or 0.0
        if configured > 0:
            return configured
        repeat_seconds = max(getattr(self.config, "repeat_minutes", 10), 1) * 60
        derived = 2 * repeat_seconds + 2 * max(interval_seconds, 1)
        return max(derived, _FLOOR_SECONDS)


def _component_of(result: MonitorResult) -> Optional[str]:
    """The component this alert is about, when the payload names one."""
    payload = result.payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        item = payload[0]
        component = item.get("id") or item.get("slug") or item.get("name")
        if component:
            return str(component)
    return None


def _rfc3339(value: datetime) -> str:
    """RFC3339 with an explicit offset, which is what the API expects."""
    return value.isoformat()


def _body_text(response) -> str:
    text = getattr(response, "text", "") or ""
    return text[:200]


def get_notifier(config) -> AlertmanagerNotifier:
    return AlertmanagerNotifier(config)
