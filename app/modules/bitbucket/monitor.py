from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus
# Parsing compartilhado: cinco modulos liam o mesmo documento com copias
# byte-identicas, e foi a duplicacao que fez o mesmo defeito precisar ser
# corrigido cinco vezes. Ver `app/core/statuspage.py`.
from ...core.statuspage import extract_components

_INCIDENTS_PATH = "/api/v2/incidents/unresolved.json"
_MAINTENANCES_PATH = "/api/v2/scheduled-maintenances/active.json"


class BitbucketStatusMonitor:
    def __init__(self, slug: str = "bitbucket") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("bitbucket monitor not configured")

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
                message="bitbucket status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000

        rule_status, rule_reason, payload, reason_items = self._evaluate_rule(data)
        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="bitbucket rule evaluation failed",
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
                message="bitbucket status degraded",
                reason=enriched_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=enriched_items,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="bitbucket status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _evaluate_rule(
        self, data: Dict
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
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

    def _evaluate_status_rule(
        self, data: Dict, rule_value: str
    ) -> tuple[MonitorStatus, Optional[str], Optional[object], Optional[List[str]]]:
        targets = {
            item.strip().lower() for item in rule_value.split(",") if item.strip()
        }
        if not targets:
            targets = {"degraded_performance", "partial_outage", "major_outage"}

        components = extract_components(data)
        if not components:
            return MonitorStatus.ERROR, "no components in status response", None, None

        filtered = components
        if self.config and self.config.service_filter:
            allow = {item.lower() for item in self.config.service_filter}
            filtered = [
                c
                for c in components
                if c["id"].lower() in allow
                or c["slug"] in allow
                or c["name"].lower() in allow
            ]
            if not filtered:
                return (
                    MonitorStatus.ERROR,
                    "no target components matched filter",
                    {"components": components, "filter": self.config.service_filter},
                    None,
                )

        matches = [c for c in filtered if c["status"].lower() in targets]
        if matches:
            items = [f"{c['name']}: {c['status']}" for c in matches]
            return MonitorStatus.ALERT, ", ".join(items), matches, items

        return MonitorStatus.OK, None, filtered, None

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
                "bitbucket enrichment fetch failed",
                extra={"url": url, "reason": str(exc)},
            )
            return None





def get_monitor(slug: str = "bitbucket") -> BitbucketStatusMonitor:
    return BitbucketStatusMonitor(slug=slug)
