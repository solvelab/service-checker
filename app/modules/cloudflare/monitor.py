"""Cloudflare Status monitor.

Statuspage v2, same shape as `github`, `bitbucket`, `openai` and `claude` — but at a
scale none of them reach. Cloudflare publishes **475 components**: 128 products plus
one per point of presence, meaning every city it has a data center in.

That scale changes one default. Everywhere else an empty `service_filter` means "watch
everything", which is a safe reading of thirteen components. Here it is not: on the day
this module was written, 51 components were non-operational — 33 in `partial_outage`,
18 in `under_maintenance` — and every one of them was a PoP: `Arica, Chile - (ARI)`,
`Kannur, India - (CNN)`, `Fortaleza, Brazil - (FOR)`. Zero products were degraded, and
the count moved between two readings hours apart. A PoP flapping is Cloudflare
working as designed: the network reroutes around it. Watching them would have fired 33
alerts across four channels on the very first cycle and taught the on-call to ignore the
feed.

So an unset filter falls back to `_DEFAULT_SERVICE_FILTER` rather than to everything.
The list is short and each entry is there for a reason we can point at. An operator who
genuinely wants all 475 sets `CLOUDFLARE_SERVICE_FILTER=*`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus

_INCIDENTS_PATH = "/api/v2/incidents/unresolved.json"
_MAINTENANCES_PATH = "/api/v2/scheduled-maintenances/active.json"

#: Watched unless the operator says otherwise. Every name was verified against the live
#: payload, and each is load-bearing for something we run:
#:
#: - ``Tunnel``                       — exposes FabCost 3D and Garimpo publicly
#: - ``Authoritative DNS``            — resolves our domains
#: - ``Network``                      — the backbone the requests cross
#: - ``CDN/Cache``                    — the layer that serves them
#: - ``SSL Certificate Provisioning`` — HTTPS on those domains
#:
#: No PoP and no continent group. Add a name here only with the same kind of reason.
_DEFAULT_SERVICE_FILTER = (
    "Tunnel",
    "Authoritative DNS",
    "Network",
    "CDN/Cache",
    "SSL Certificate Provisioning",
)

#: Opt out of the default and watch every component, PoPs included.
_WATCH_EVERYTHING = "*"


class CloudflareStatusMonitor:
    def __init__(self, slug: str = "cloudflare") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("cloudflare monitor not configured")

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
                message="cloudflare status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000

        rule_status, rule_reason, payload, reason_items = self._evaluate_rule(data, logger)
        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="cloudflare rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        if rule_status == MonitorStatus.ALERT:
            enriched_reason, enriched_items = await self._enrich_reason(
                rule_reason, reason_items, http_client, logger
            )
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="cloudflare status degraded",
                reason=enriched_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=enriched_items,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="cloudflare status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _watchlist(self) -> Optional[set]:
        """The names to watch, lowercased — or None to watch every component."""
        configured = [
            item.strip() for item in (self.config.service_filter if self.config else [])
        ]
        if _WATCH_EVERYTHING in configured:
            return None
        # Blanks are dropped before deciding whether anything was configured: an env var
        # left as `CLOUDFLARE_SERVICE_FILTER=,` must fall back to the curated default,
        # not to an empty watchlist that matches nothing and reports a filter error.
        configured = [item for item in configured if item]
        if not configured:
            configured = list(_DEFAULT_SERVICE_FILTER)
        return {item.lower() for item in configured}

    def _evaluate_rule(
        self, data: Dict, logger: logging.Logger
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None, None

        rule_kind = self.config.rule.kind
        rule_value = self.config.rule.value

        if rule_kind == "status":
            return self._evaluate_status_rule(data, rule_value, logger)

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
        self, data: Dict, rule_value: str, logger: logging.Logger
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        targets = {
            item.strip().lower() for item in (rule_value or "").split(",") if item.strip()
        }
        if not targets:
            targets = {"degraded_performance", "partial_outage", "major_outage"}

        components = _extract_components(data)
        if not components:
            return MonitorStatus.ERROR, "no components in status response", None, None

        allow = self._watchlist()
        if allow is None:
            filtered = components
        else:
            filtered = [
                c
                for c in components
                if c["id"].lower() in allow
                or c["slug"] in allow
                or c["name"].lower() in allow
            ]
            self._warn_about_missing(allow, components, logger)
            if not filtered:
                return (
                    MonitorStatus.ERROR,
                    "no target components matched filter",
                    {"components": components, "filter": sorted(allow)},
                    None,
                )

        matches = [c for c in filtered if c["status"].lower() in targets]
        if matches:
            items = [f"{c['name']}: {c['status']}" for c in matches]
            return MonitorStatus.ALERT, ", ".join(items), matches, items

        return MonitorStatus.OK, None, filtered, None

    def _warn_about_missing(
        self, allow: set, components: List[Dict], logger: logging.Logger
    ) -> None:
        """Say so when a watched name is absent from the payload.

        Cloudflare renaming a component would otherwise shrink the watchlist in silence:
        the module keeps returning OK, and it is right about the components it still
        matches — which is exactly how a monitor goes blind while looking healthy.
        """
        known = set()
        for comp in components:
            known.update({comp["id"].lower(), comp["slug"], comp["name"].lower()})
        missing = sorted(allow - known)
        if not missing:
            return
        logger.warning(
            "watched component not found in status payload",
            extra={
                "event": "component_missing",
                "module_id": self.id,
                "check_id": self.id,
                "reason": f"absent from upstream: {', '.join(missing)}",
            },
        )

    async def _enrich_reason(
        self,
        base_reason: Optional[str],
        base_items: Optional[List[str]],
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> tuple[Optional[str], Optional[List[str]]]:
        """Fetch incidents and maintenances to enrich the alert reason.

        Returns the joined sentence and the same content as one entry per finding.
        Splitting the sentence back apart is not an option: incident titles routinely
        contain commas and semicolons.
        """
        if self.config is None or base_reason is None:
            return base_reason, base_items

        base_url = self.config.url.rsplit("/api/", 1)[0]
        parts = list(base_items) if base_items else [base_reason]
        base_len = len(parts)

        incidents = await self._fetch_extra(
            http_client, f"{base_url}{_INCIDENTS_PATH}", logger
        )
        if incidents is not None:
            for inc in incidents.get("incidents", [])[:3]:
                name = inc.get("name", "unknown")
                status = inc.get("status", "unknown")
                updated = inc.get("updated_at") or inc.get("created_at") or ""
                parts.append(f"Incident: {name} ({status}, {updated})")

        maintenances = await self._fetch_extra(
            http_client, f"{base_url}{_MAINTENANCES_PATH}", logger
        )
        if maintenances is not None:
            for mnt in maintenances.get("scheduled_maintenances", [])[:3]:
                name = mnt.get("name", "unknown")
                status = mnt.get("status", "unknown")
                scheduled = mnt.get("scheduled_for") or mnt.get("updated_at") or ""
                parts.append(f"Maintenance: {name} ({status}, {scheduled})")

        if len(parts) == base_len:
            # Nothing was enriched: leave `reason` byte-identical to what the
            # rule produced, so the webhook contract does not shift.
            return base_reason, base_items
        return "; ".join(parts), parts

    async def _fetch_extra(
        self,
        http_client: httpx.AsyncClient,
        url: str,
        logger: logging.Logger,
    ) -> Optional[Dict]:
        """Fetch an extra endpoint; return None on any failure."""
        try:
            resp = await http_client.get(
                url,
                timeout=self.config.timeout_seconds if self.config else 10.0,
                headers={
                    "User-Agent": (
                        self.config.user_agent
                        if self.config
                        else "service-checker/1.0"
                    )
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "cloudflare enrichment fetch failed",
                extra={"url": url, "reason": str(exc)},
            )
            return None


def _extract_components(data: Dict) -> List[Dict]:
    components = data.get("components") or []
    # Shape, not just presence. `components` arriving as a dict or holding strings used
    # to raise `AttributeError` from here — and this runs outside the request's
    # try/except, so the exception left `check` entirely. The scheduler does catch it,
    # but by then the module never returned a MonitorStatus.ERROR, so the dead-monitor
    # notification never fired and the breakage lived in the log alone.
    if not isinstance(components, list):
        return []
    cleaned: List[Dict] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        name = comp.get("name") or "unknown"
        comp_id = comp.get("id") or _slugify(name)
        status = comp.get("status") or "unknown"
        cleaned.append(
            {
                "id": comp_id,
                "name": name,
                "status": status,
                "slug": _slugify(name),
            }
        )
    return cleaned


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def get_monitor(slug: str = "cloudflare") -> CloudflareStatusMonitor:
    return CloudflareStatusMonitor(slug=slug)
