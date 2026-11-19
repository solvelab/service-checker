"""ElevenLabs status monitor (#117).

`status.elevenlabs.io` is an incident.io page that serves a Statuspage-compatible
`api/v2/summary.json` — with an empty `components` list. On 2026-11-18 the page carried
the indicator `minor` and one open incident, `Increased latency and API errors`, with no
component attached; `api/v2/incidents/unresolved.json` answered 404. A Statuspage module
(`claude`, `bitbucket`) demands components and would sit in ERROR on every check.

So this module reads what the page does publish: the page indicator decides whether to
alert, and every open incident becomes one payload item, so each incident alerts and
recovers on its own (the manager keys per-service state by the item's `id`).

Regions (#136): the page has four regional sub-pages (`united-states`, `eu`, `india`,
`singapore`) and `summary.json` aggregates their incidents without saying which is
which. Only incident.io's undocumented frontend API maps an incident to its regions:
`/proxy/<host>` lists the sub-pages, `/proxy/<host>/incidents?subpage_slug=<subpath>`
lists one region's incidents with the same ids as `api/v2`. It is read only when an
incident is open (or a region filter needs the catalogue), and when it fails the alert
goes out as before, without the region: the documented API stays the source of truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit

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
        # Per open incident, across cycles (#136 bug-hunter): the regions seen so far and,
        # with a filter, whether it was let in. A lookup that flaps would otherwise drop
        # an incident one cycle (a false "restored") and send it again the next.
        self._known_regions: Dict[str, List[str]] = {}
        self._decisions: Dict[str, bool] = {}
        self._lookup_error: Optional[str] = None

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

        regions = None
        if self.config.rule.kind == "status":
            # Only an incident that can alert is worth ~450 KB of regional listings.
            targets = _targets(self.config.rule.value)
            alerting = any(_alerts(i["impact"], targets) for i in _open_incidents(data))
            if alerting or self.config.service_filter:
                regions = await self._lookup_regions(http_client, logger, need_incidents=alerting)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status, reason, payload, items = self._evaluate_rule(data, regions)
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

    async def _lookup_regions(
        self, http_client: httpx.AsyncClient, logger: logging.Logger, *, need_incidents: bool
    ) -> Optional["RegionLookup"]:
        """Catalogue and, when an incident is open, incident ids per region; None on failure."""
        parts = urlsplit(self.config.url)
        base = f"{parts.scheme}://{parts.netloc}/proxy/{parts.netloc}"
        headers = {"User-Agent": self.config.user_agent}
        errors: List[str] = []
        membership: Dict[str, List[str]] = {}
        try:
            catalog = _parse_catalog(await self._get_json(http_client, base, headers))
        except Exception as exc:  # noqa: BLE001
            self._report_lookup(logger, f"catalogue: {type(exc).__name__}: {exc}")
            return None
        if need_incidents:
            # return_exceptions: one failing region keeps the others' answers, and no
            # request is left running past the check.
            listings = await asyncio.gather(*(
                self._listing(http_client, f"{base}/incidents", headers, subpath)
                for subpath, _name in catalog
            ), return_exceptions=True)
            for (subpath, name), listing in zip(catalog, listings):
                if isinstance(listing, BaseException):
                    errors.append(f"{subpath}: {type(listing).__name__}: {listing}")
                    continue
                for incident_id in listing:
                    membership.setdefault(incident_id, []).append(name)
        self._report_lookup(logger, "; ".join(errors) or None)
        return RegionLookup(catalog=catalog, membership=membership)

    async def _listing(self, http_client, url, headers, subpath) -> List[str]:
        return _parse_incident_ids(
            await self._get_json(http_client, url, headers, params={"subpage_slug": subpath})
        )

    def _report_lookup(self, logger: logging.Logger, error: Optional[str]) -> None:
        """Log a failing lookup once per streak, and its recovery once."""
        if error and self._lookup_error is None:
            logger.warning(
                "elevenlabs region lookup failed; regions may be missing",
                extra={"event": "region_lookup_failed", "module_id": self.id, "reason": error},
            )
        elif error is None and self._lookup_error is not None:
            logger.info(
                "elevenlabs region lookup working again",
                extra={"event": "region_lookup_recovered", "module_id": self.id},
            )
        self._lookup_error = error

    async def _get_json(self, http_client, url, headers, params=None):
        response = await http_client.get(
            url, params=params, timeout=self.config.timeout_seconds, headers=headers
        )
        response.raise_for_status()
        return response.json()

    def _evaluate_rule(self, data: object, regions: Optional["RegionLookup"] = None) -> Evaluation:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None, None

        rule_kind = self.config.rule.kind
        rule_value = self.config.rule.value

        if rule_kind == "status":
            return self._evaluate_status_rule(data, rule_value, regions)

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

    def _evaluate_status_rule(
        self, data: object, rule_value: str, regions: Optional["RegionLookup"] = None
    ) -> Evaluation:
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

        region_filter = [v.strip().lower() for v in self.config.service_filter if v.strip()]
        if region_filter and regions is not None:
            known = {s.lower() for s, _ in regions.catalog} | {n.lower() for _, n in regions.catalog}
            unknown = [v for v in region_filter if v not in known]
            if unknown:
                # The API answers 200 and empty for a sub-page that does not exist: a
                # typo would leave the module blind without a word.
                return (MonitorStatus.ERROR,
                        f"unknown region(s) in filter: {', '.join(unknown)}; known: "
                        f"{', '.join(s for s, _ in regions.catalog)}",
                        None, None)

        targets = _targets(rule_value)
        items: List[Dict] = []
        # The page item lives exactly as long as the indicator is raised, alongside the
        # incidents rather than instead of them: swapping one for the other turned one
        # outage into a recovery and a fresh alert at each swap (bug-hunter). With a
        # region filter it is left out: the indicator aggregates every region.
        if not region_filter and _alerts(indicator, targets):
            items.append({
                "id": "page",
                "name": "ElevenLabs",
                "status": indicator,
                "status_text": description,
            })
        # An open incident stays an item until it closes, whatever the indicator does
        # meanwhile; its own impact decides whether it is severe enough.
        open_incidents = _open_incidents(data)
        open_ids = {inc["incident_id"] for inc in open_incidents}
        for key in [k for k in self._known_regions if k not in open_ids]:
            del self._known_regions[key]
        for key in [k for k in self._decisions if k not in open_ids]:
            del self._decisions[key]
        for inc in open_incidents:
            if not _alerts(inc["impact"], targets):
                continue
            names = self._regions_of(inc["incident_id"], regions)
            if region_filter and inc["incident_id"]:
                # The first decision holds while the incident is open: an incident not
                # yet in any listing (or met while the lookup fails) is let in — a false
                # positive beats a blind module — and stays in, so it never "recovers"
                # just because its region showed up outside the filter.
                decision = self._decisions.get(inc["incident_id"])
                if decision is None:
                    decision = (not names) or RegionLookup.matches_catalog(
                        regions.catalog if regions is not None else [], names, region_filter)
                    self._decisions[inc["incident_id"]] = decision
                if not decision:
                    continue
            if names:
                inc["regions"] = names
                inc["name"] = f"{inc['name']} [{', '.join(names)}]"
            items.append(inc)
        items += components

        if not items:
            return MonitorStatus.OK, None, [], None
        lines = [f"{item['name']}: {item.get('status_text') or item['status']}"
                 for item in items]
        return MonitorStatus.ALERT, "; ".join(lines), items, lines

    def _regions_of(self, incident_id: str, regions: Optional["RegionLookup"]) -> List[str]:
        """Regions seen for this incident so far, merged with this cycle's lookup."""
        if not incident_id:
            return []
        known = self._known_regions.setdefault(incident_id, [])
        for name in regions.regions_of(incident_id) if regions is not None else []:
            if name not in known:
                known.append(name)
        return list(known)

    def _degraded_components(self, data: object):
        """Degraded published components, or an ERROR result for a filter that matches none.

        The page publishes none today; this keeps working the day it starts.
        """
        components = extract_components(data)
        if not components:
            return []
        # SERVICE_FILTER names regions (#136); components are not filtered.
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
            "incident_id": str(inc.get("id") or ""),
            "name": name,
            "status": status,
            "impact": str(inc.get("impact") or "unknown").strip().lower(),
            "updated_at": inc.get("updated_at") or inc.get("created_at") or "",
        })
    return items


class RegionLookup:
    """Region catalogue (subpath, name) and the region names listing each incident id."""

    def __init__(self, catalog: List[tuple], membership: Dict[str, List[str]]) -> None:
        self.catalog = catalog
        self.membership = membership

    def regions_of(self, incident_id: str) -> List[str]:
        return list(self.membership.get(incident_id, [])) if incident_id else []

    @staticmethod
    def matches_catalog(catalog: List[tuple], names: List[str], region_filter: List[str]) -> bool:
        wanted = {n.lower() for s, n in catalog
                  if s.lower() in region_filter or n.lower() in region_filter}
        wanted |= set(region_filter)  # names cached from an earlier catalogue
        return any(name.lower() in wanted for name in names)


def _parse_catalog(data: object) -> List[tuple]:
    """(subpath, name) per region; a changed shape raises, which the lookup reports."""
    subpages = data.get("subpages") if isinstance(data, dict) else None
    if not isinstance(subpages, list) or not subpages:
        raise ValueError("no subpages in proxy response")
    catalog = [(str(s["subpath"]), str(s.get("name") or s["subpath"]))
               for s in subpages if isinstance(s, dict) and s.get("subpath")]
    if not catalog:
        raise ValueError("no subpage with a subpath in proxy response")
    return catalog


def _parse_incident_ids(data: object) -> List[str]:
    incidents = data.get("incidents") if isinstance(data, dict) else None
    if not isinstance(incidents, list):
        raise ValueError("no incidents list in regional response")
    return [str(i["id"]) for i in incidents if isinstance(i, dict) and i.get("id")]


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
