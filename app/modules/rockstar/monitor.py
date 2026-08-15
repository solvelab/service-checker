from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus

_HERO_HEADING_RE = re.compile(
    r'data-testid="status-hero-heading"[^>]*>([^<]+)<', re.IGNORECASE
)
_HERO_METADATA_RE = re.compile(
    r'data-testid="status-hero-metadata"[^>]*>([^<]+)<', re.IGNORECASE
)
_SECTION_OR_ITEM_RE = re.compile(
    r'data-testid="(status-section-heading|status-item-\d+)"[^>]*>(.*?)'
    r'(?=data-testid="(?:status-section-heading|status-item-\d+)"|</body>)',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Impersonation profiles age out: chrome110 is already refused by steamstat.us,
# another Cloudflare-fronted endpoint. Both chrome110 and chrome124 are accepted
# here (measured 2026-08-15); pinning the newer one keeps both modules aligned on
# a profile that is still current.
_DEFAULT_IMPERSONATE = "chrome124"


class RockstarStatusMonitor:
    def __init__(self, slug: str = "rockstar") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None
        self._impersonate = os.getenv(
            "ROCKSTAR_IMPERSONATE_PROFILE", _DEFAULT_IMPERSONATE
        )

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("rockstar monitor not configured")

        start = time.perf_counter()
        try:
            html = await asyncio.to_thread(self._fetch_html)
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="rockstar status request failed",
                reason=f"{type(exc).__name__}: {exc}",
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000
        return self._evaluate(html, duration_ms)

    def _fetch_html(self) -> str:
        # Imported lazily so the rest of the app does not hard-depend on curl_cffi.
        from curl_cffi import requests as cffi_requests

        assert self.config is not None
        response = cffi_requests.get(
            self.config.url,
            impersonate=self._impersonate,
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"upstream returned HTTP {response.status_code}"
            )
        text = response.text
        if not text:
            raise RuntimeError("upstream returned empty body")
        return text

    def _evaluate(self, html: str, duration_ms: float) -> MonitorResult:
        hero_match = _HERO_HEADING_RE.search(html)
        if hero_match is None:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="rockstar parse failed",
                reason="missing status-hero-heading",
                duration_ms=round(duration_ms, 2),
            )

        hero_text = hero_match.group(1).strip()
        metadata_match = _HERO_METADATA_RE.search(html)
        updated_at = metadata_match.group(1).strip() if metadata_match else None

        services = _extract_services(html)
        if not services:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="rockstar parse failed",
                reason="no status items found",
                duration_ms=round(duration_ms, 2),
                payload={"hero": hero_text, "updated_at": updated_at},
            )

        filter_list = self.config.service_filter if self.config else []
        filtered = _apply_filter(services, filter_list)
        if filter_list and not filtered:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="rockstar filter unmatched",
                reason="no target services matched filter",
                duration_ms=round(duration_ms, 2),
                payload={
                    "hero": hero_text,
                    "updated_at": updated_at,
                    "filter": filter_list,
                    "services": services,
                },
            )

        degraded = [s for s in filtered if s["status"] != "operational"]
        hero_ok = "operational" in hero_text.lower()

        payload: Dict[str, Any] = {
            "hero": hero_text,
            "updated_at": updated_at,
            "services": filtered,
        }

        if degraded:
            reason = ", ".join(
                f"{s['name']}: {s['status_text']}" for s in degraded
            )
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="rockstar status degraded",
                reason=reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        if not hero_ok and not filter_list:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="rockstar status degraded",
                reason=hero_text,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="rockstar status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )


def _extract_services(html: str) -> List[Dict[str, Any]]:
    services: List[Dict[str, Any]] = []
    current_section = "Unknown"
    section_index = 0
    item_index = 0
    for testid, raw in _SECTION_OR_ITEM_RE.findall(html):
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw)).strip()
        if testid == "status-section-heading":
            section_index += 1
            item_index = 0
            current_section = _first_phrase(text)
            continue
        item_index += 1
        name, status_text = _split_item(text)
        services.append(
            {
                "id": _slugify(f"{current_section}-{name}"),
                "name": name,
                "section": current_section,
                "status_text": status_text,
                "status": _classify(status_text),
            }
        )
    return services


_STATUS_PHRASE_RE = re.compile(
    r"\b(All services\s+\S+|There (?:is|are)\s+[^.]+|"
    r"(?:Partial|Major|Minor|Scheduled)\s+\S+|"
    r"\S*(?:operational|outage|down|maintenance|issue)\S*\b[^.]*)",
    re.IGNORECASE,
)


def _split_item(text: str) -> Tuple[str, str]:
    # Items render as "<name> <status phrase>". The status phrase begins with
    # one of the known indicator patterns ("All services …", "There are …",
    # or a token containing operational/outage/down/maintenance/issue).
    match = _STATUS_PHRASE_RE.search(text)
    if not match or match.start() == 0:
        return text.strip(), _strip_residue(text)
    name = text[: match.start()].strip().rstrip(":-").strip()
    status = _strip_residue(text[match.start() :])
    return name or text.strip(), status


def _strip_residue(value: str) -> str:
    # Section/item raw chunks may end with a partial tag like "<p" or "<h6"
    # due to the testid lookahead. Trim anything from the first '<' onward.
    return value.split("<")[0].strip()


def _classify(status_text: str) -> str:
    lower = status_text.lower()
    if "operational" in lower:
        return "operational"
    if "maintenance" in lower:
        return "maintenance"
    if "down" in lower or "outage" in lower or "issue" in lower:
        return "down"
    return "unknown"


def _apply_filter(
    services: List[Dict[str, Any]], filter_list: List[str]
) -> List[Dict[str, Any]]:
    if not filter_list:
        return services
    allow = {item.lower() for item in filter_list}
    return [
        s
        for s in services
        if s["id"] in allow
        or s["name"].lower() in allow
        or s["section"].lower() in allow
    ]


def _first_phrase(text: str) -> str:
    cleaned = text.split("<")[0]
    return cleaned.strip() or text.strip()


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def get_monitor(slug: str = "rockstar") -> RockstarStatusMonitor:
    return RockstarStatusMonitor(slug=slug)
