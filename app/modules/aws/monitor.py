import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus

# The public feed carries no typeCode/region/endTime fields — everything the rule
# engine needs is encoded in the event ARN:
#
#   arn:aws:health:eu-central-1::event/DIRECTCONNECT/AWS_DIRECTCONNECT_OPERATIONAL_ISSUE/
#                  └── region ──┘                    └──────── type code ──────────┘
#   AWS_DIRECTCONNECT_OPERATIONAL_ISSUE_E23AA_ADAB9D3D11F
#   └──────────── unique event id ─────────────────────┘
#
# Verified against the live endpoint on 2026-08-15.
_ARN_RE = re.compile(
    r"^arn:aws:health:(?P<region>[^:]*):[^:]*:event/(?P<category>[^/]+)/"
    r"(?P<type_code>[^/]+)/(?P<event_id>.+)$",
    re.IGNORECASE,
)
# Fallback for the region: the service code is "<servicecode>-<region-code>".
_SERVICE_REGION_RE = re.compile(
    r"-(?P<region>[a-z]{2}(?:-[a-z]+)+-\d+)$", re.IGNORECASE
)


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
        rule_status, rule_reason, payload, reason_items = self._evaluate_rule(data)

        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="aws rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        if rule_status == MonitorStatus.ALERT:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="aws status degraded",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="aws status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _evaluate_rule(
        self, data: object
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None, None

        if not isinstance(data, list):
            return MonitorStatus.ERROR, "unexpected incidents payload", None, None

        targets = {
            item.strip().lower()
            for item in (self.config.service_filter or [])
            if item.strip()
        }
        code_tokens = {
            item.strip().lower()
            for item in (self.config.rule.value or "").split(",")
            if item.strip()
        }
        if not code_tokens:
            code_tokens = {"operational_issue"}

        active_events: List[Dict] = []
        for event in data:
            if not isinstance(event, dict):
                continue

            parsed = _parse_event(event)

            # Everything the endpoint returns is by definition a current event: it
            # exposes no end timestamp and no resolved entries (verified 2026-08-15).
            if not _matches_filter(parsed, targets):
                continue

            type_code = (parsed["type_code"] or "").lower()
            if type_code and not any(token in type_code for token in code_tokens):
                continue

            active_events.append(parsed)

        if active_events:
            items = [
                f"{evt['region_name'] or evt['region']} / {evt['name']}: {evt['summary']}"
                for evt in active_events
            ]
            return MonitorStatus.ALERT, "; ".join(items), active_events, items

        return MonitorStatus.OK, None, [], None


def _parse_event(event: Dict) -> Dict:
    """Normalise one raw AWS event into the payload shape the notifier expects."""
    arn = event.get("arn") or ""
    match = _ARN_RE.match(arn)
    region = match.group("region") if match else ""
    type_code = match.group("type_code") if match else ""
    event_id = match.group("event_id") if match else ""

    service_code = event.get("service") or ""
    if not region:
        service_match = _SERVICE_REGION_RE.search(service_code)
        region = service_match.group("region") if service_match else ""

    return {
        "id": _slugify(event_id) or _slugify(arn) or _slugify(service_code),
        "name": event.get("service_name") or service_code or "unknown",
        "service": service_code,
        "region": region,
        "region_name": event.get("region_name") or "",
        "type_code": type_code,
        # Undocumented numeric severity ("1", "2", "3" observed; higher looks worse).
        # Carried through as metadata only — never used to decide whether to alert.
        "status": str(event.get("status") or "unknown"),
        "summary": event.get("summary") or "",
        "started_at": event.get("date") or "",
    }


def _matches_filter(event: Dict, targets: set) -> bool:
    if not targets:
        return True
    candidates = {
        (event.get("region") or "").lower(),
        (event.get("region_name") or "").lower(),
        (event.get("service") or "").lower(),
        (event.get("name") or "").lower(),
    }
    candidates.discard("")
    return bool(candidates & targets)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def get_monitor(slug: str = "aws") -> AwsStatusMonitor:
    return AwsStatusMonitor(slug=slug)
