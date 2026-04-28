import logging
import re
import time
from typing import Dict, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus


class AwsStatusMonitor:
    def __init__(self, slug: str = "aws") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("aws monitor not configured")

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
                message="aws status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000
        rule_status, rule_reason, payload = self._evaluate_rule(data)

        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="aws rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        if rule_status == MonitorStatus.ALERT:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="aws status degraded",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="aws status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _evaluate_rule(self, data: object) -> tuple[MonitorStatus, Optional[str], Optional[object]]:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None

        if not isinstance(data, list):
            return MonitorStatus.ERROR, "unexpected incidents payload", None

        targets = {item.strip().lower() for item in (self.config.service_filter or []) if item.strip()}
        code_tokens = {item.strip().lower() for item in (self.config.rule.value or "").split(",") if item.strip()}
        if not code_tokens:
            code_tokens = {"operational_issue"}

        active_events = []
        for event in data:
            if not isinstance(event, dict):
                continue
            region = (event.get("region") or "").lower()
            if targets and region not in targets:
                continue

            end_time = event.get("endTime")
            if end_time:
                continue

            type_code = (event.get("typeCode") or "").lower()
            status_code = _extract_status_code(event)

            if not type_code:
                continue

            if not any(token in type_code for token in code_tokens):
                continue

            active_events.append(
                {
                    "service": event.get("service"),
                    "region": event.get("region"),
                    "typeCode": event.get("typeCode"),
                    "status": status_code,
                    "startTime": event.get("startTime"),
                }
            )

        if active_events:
            reason = "; ".join(
                f"{evt.get('region')}: {evt.get('typeCode') or evt.get('status') or 'active'}"
                for evt in active_events
            )
            return MonitorStatus.ALERT, reason, active_events

        return MonitorStatus.OK, None, []


def _extract_status_code(event: Dict) -> str:
    status = event.get("status")
    if status is not None:
        return str(status)
    type_code = event.get("typeCode") or ""
    match = re.search(r"(operational_issue|availability|performance|degradation)", type_code, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return "unknown"


def get_monitor(slug: str = "aws") -> AwsStatusMonitor:
    return AwsStatusMonitor(slug=slug)
