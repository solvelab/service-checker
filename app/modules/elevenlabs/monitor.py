"""ElevenLabs status monitor (#117).

`status.elevenlabs.io` is an incident.io page that serves a Statuspage-compatible
`api/v2/summary.json` — with an empty `components` list. On 2026-11-18 the page carried
the indicator `minor` and one open incident, `Increased latency and API errors`, with no
component attached; `api/v2/incidents/unresolved.json` answered 404. A Statuspage module
(`claude`, `bitbucket`) demands components and would sit in ERROR on every check.

So this module reads what the page does publish: the page indicator decides whether to
alert, and every open incident becomes one payload item, so each incident alerts and
recovers on its own (the manager keys per-service state by the item's `id`).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.statuspage import extract_components
from ...core.types import MonitorResult, MonitorStatus

#: Page indicator severities, lowest first. `RULE_VALUE` names the minimum that alerts.
_SEVERITY = {"minor": 1, "major": 2, "critical": 3}
#: Every indicator the page is known to publish; anything else is a changed source.
_INDICATORS = frozenset(_SEVERITY) | {"none", "maintenance"}
_DEGRADED = frozenset({"degraded_performance", "partial_outage", "major_outage"})
_CLOSED_INCIDENT = frozenset({"resolved", "postmortem", "completed"})

Evaluation = tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]


class ElevenLabsStatusMonitor:
    def __init__(self, slug: str = "elevenlabs") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("elevenlabs monitor not configured")

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
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="elevenlabs status request failed",
                reason=str(exc),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status, reason, payload, items = self._evaluate_rule(data)
        message = {
            MonitorStatus.ERROR: "elevenlabs rule evaluation failed",
            MonitorStatus.ALERT: "elevenlabs status degraded",
            MonitorStatus.OK: "elevenlabs status healthy",
        }[status]
        return MonitorResult(
            status=status,
            message=message,
            reason=reason,
            duration_ms=duration_ms,
            payload=payload,
            reason_items=items,
        )

    def _evaluate_rule(self, data: object) -> Evaluation:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None, None

        rule_kind = self.config.rule.kind
        rule_value = self.config.rule.value

        if rule_kind == "status":
            return self._evaluate_status_rule(data, rule_value)

        raw_text = json.dumps(data)
        if rule_kind == "keyword":
            if rule_value.lower() in raw_text.lower():
                return MonitorStatus.ALERT, f"keyword '{rule_value}' detected", None, None
            return MonitorStatus.OK, None, None, None
        if rule_kind == "regex":
            try:
                pattern = re.compile(rule_value, re.IGNORECASE)
            except re.error as exc:
                return MonitorStatus.ERROR, f"invalid regex: {exc}", None, None
            if pattern.search(raw_text) is not None:
                return MonitorStatus.ALERT, f"regex '{rule_value}' matched", None, None
            return MonitorStatus.OK, None, None, None

        return MonitorStatus.ERROR, f"unsupported rule kind '{rule_kind}'", None, None

    def _evaluate_status_rule(self, data: object, rule_value: str) -> Evaluation:
        page = data.get("status") if isinstance(data, dict) else None
        indicator = page.get("indicator") if isinstance(page, dict) else None
        if not isinstance(indicator, str):
            # The one field the rule stands on: without it the source changed shape,
            # and reading that as healthy would blind the module.
            return MonitorStatus.ERROR, "no status indicator in status response", None, None
        indicator = indicator.strip().lower()
        if indicator not in _INDICATORS:
            # An empty or renamed vocabulary is a changed source, not a healthy page:
            # read as OK it would also announce every open incident restored.
            return (MonitorStatus.ERROR,
                    f"unknown status indicator {indicator!r} in status response",
                    None, None)
        description = page.get("description")
        description = description.strip() if isinstance(description, str) else ""
        description = description or indicator

        components = self._degraded_components(data)
        if isinstance(components, MonitorResult):
            return components.status, components.reason, components.payload, None

        targets = _targets(rule_value)
        items: List[Dict] = []
        # The page item lives exactly as long as the indicator is raised, alongside the
        # incidents rather than instead of them: swapping one for the other turned one
        # outage into a recovery and a fresh alert at each swap (bug-hunter).
        if _alerts(indicator, targets):
            items.append({
                "id": "page",
                "name": "ElevenLabs",
                "status": indicator,
                "status_text": description,
            })
        # An open incident stays an item until it closes, whatever the indicator does
        # meanwhile; its own impact decides whether it is severe enough.
        items += [inc for inc in _open_incidents(data) if _alerts(inc["impact"], targets)]
        items += components

        if not items:
            return MonitorStatus.OK, None, [], None
        lines = [f"{item['name']}: {item.get('status_text') or item['status']}"
                 for item in items]
        return MonitorStatus.ALERT, "; ".join(lines), items, lines

    def _degraded_components(self, data: object):
        """Degraded published components, or an ERROR result for a filter that matches none.

        The page publishes none today; this keeps working the day it starts.
        """
        components = extract_components(data)
        if not components:
            return []
        if self.config and self.config.service_filter:
            allow = {item.lower() for item in self.config.service_filter}
            filtered = [c for c in components
                        if c["id"].lower() in allow or c["slug"] in allow
                        or c["name"].lower() in allow]
            if not filtered:
                return MonitorResult(
                    status=MonitorStatus.ERROR,
                    message="",
                    reason="no target components matched filter",
                    payload={"components": components, "filter": self.config.service_filter},
                )
            components = filtered
        return [dict(c, id=f"component:{c['id']}") for c in components
                if c["status"].lower() in _DEGRADED]


def _open_incidents(data: object) -> List[Dict]:
    """Open incidents as payload items; entries that are not objects are skipped."""
    incidents = data.get("incidents") if isinstance(data, dict) else None
    if not isinstance(incidents, list):
        return []
    items = []
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        status = str(inc.get("status") or "unknown").strip().lower()
        if status in _CLOSED_INCIDENT:
            continue
        name = str(inc.get("name") or "unknown incident")
        items.append({
            # The manager keys alert state by `id`: a new incident alerts on its own,
            # and one that leaves the page recovers on its own. The prefix keeps an
            # incident from sharing a key with the page or a component.
            "id": f"incident:{inc.get('id') or name}",
            "name": name,
            "status": status,
            "impact": str(inc.get("impact") or "unknown").strip().lower(),
            "updated_at": inc.get("updated_at") or inc.get("created_at") or "",
        })
    return items


def _targets(rule_value: str) -> set:
    return {item.strip().lower() for item in rule_value.split(",") if item.strip()}


def _alerts(level: str, targets: set) -> bool:
    """Whether an indicator or an incident impact reaches the configured minimum.

    The lowest severity listed wins, so the generic default `major,minor` reads as
    "minor and up", and a value naming no severity reads as `minor`. A level outside
    the scale (`maintenance`) alerts only when listed; `none` never. An impact nobody
    knows still names an open incident, so it is shown.
    """
    if level == "none":
        return False
    if level in _SEVERITY:
        known = [_SEVERITY[t] for t in targets if t in _SEVERITY]
        return _SEVERITY[level] >= (min(known) if known else _SEVERITY["minor"])
    return level in targets or level not in _INDICATORS


def get_monitor(slug: str = "elevenlabs") -> ElevenLabsStatusMonitor:
    return ElevenLabsStatusMonitor(slug=slug)
