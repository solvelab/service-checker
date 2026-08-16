import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus


class OciStatusMonitor:
    def __init__(self, slug: str = "oci") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("oci monitor not configured")

        start = time.perf_counter()
        try:
            response = await http_client.get(
                self.config.url,
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": self.config.user_agent},
            )
            response.raise_for_status()
            xml_body = response.text
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="oci status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000

        rule_status, rule_reason, payload = self._evaluate_rule(xml_body)
        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="oci rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        if rule_status == MonitorStatus.ALERT:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="oci status degraded",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="oci status healthy",
            duration_ms=round(duration_ms, 2),
            payload=payload,
        )

    def _evaluate_rule(self, xml_body: str) -> tuple[MonitorStatus, Optional[str], Optional[object]]:
        if self.config is None:
            return MonitorStatus.ERROR, "missing config", None

        try:
            incidents = _parse_incidents(xml_body)
        except Exception as exc:  # noqa: BLE001
            return MonitorStatus.ERROR, f"failed to parse feed: {exc}", None

        filtered_incidents = _filter_incidents(incidents, self.config.service_filter)
        rule_kind = self.config.rule.kind
        rule_value = self.config.rule.value

        if rule_kind == "status":
            return self._evaluate_status_rule(filtered_incidents, rule_value)

        if not rule_value:
            return MonitorStatus.OK, None, filtered_incidents

        if rule_kind == "keyword":
            if rule_value.lower() in xml_body.lower():
                return MonitorStatus.ALERT, f"keyword '{rule_value}' detected", None
            return MonitorStatus.OK, None, filtered_incidents

        if rule_kind == "regex":
            try:
                pattern = re.compile(rule_value, re.IGNORECASE)
            except re.error as exc:
                return MonitorStatus.ERROR, f"invalid regex: {exc}", None
            if pattern.search(xml_body) is not None:
                return MonitorStatus.ALERT, f"regex '{rule_value}' matched", None
            return MonitorStatus.OK, None, filtered_incidents

        return MonitorStatus.ERROR, f"unsupported rule kind '{rule_kind}'", None

    def _evaluate_status_rule(
        self, incidents: List[Dict], rule_value: str
    ) -> tuple[MonitorStatus, Optional[str], Optional[object]]:
        targets = {item.strip().lower() for item in (rule_value or "").split(",") if item.strip()}
        if not targets:
            targets = {"investigating", "identified", "monitoring"}

        matches = [
            incident
            for incident in incidents
            if incident.get("status") and incident["status"].lower() in targets
        ]

        if matches:
            reason = ", ".join(
                f"{incident['region'] or incident['service']}: {incident['status']}"
                for incident in matches
            )
            return MonitorStatus.ALERT, reason, matches

        return MonitorStatus.OK, None, incidents


def _parse_incidents(xml_body: str) -> List[Dict]:
    root = ET.fromstring(xml_body)
    incidents: List[Dict] = []

    for item in root.findall(".//item"):
        title_text = (item.findtext("title") or "").strip()
        service, region, reference = _split_title(title_text)
        description = item.findtext("description") or ""
        link = (item.findtext("link") or "").strip()
        incidents.append(
            {
                "id": _incident_id(reference, title_text, link),
                # `name` keeps the Telegram card readable: without it the notifier
                # falls back to the id and shows a bare hex reference.
                "name": service or title_text or "unknown",
                "title": title_text,
                "service": service,
                "region": region,
                "reference": reference,
                "status": _extract_status(description),
                "link": link,
            }
        )

    return incidents


def _incident_id(reference: str, title: str, link: str) -> str:
    """Stable per-incident identifier.

    The alert state of every component is keyed on this, so it must be identical
    across cycles for the same incident and different for different ones. OCI puts
    a unique reference in the third segment of the title (e.g. ``210f910e``); the
    fallbacks cover titles that do not carry one.
    """
    for candidate in (reference, link, title):
        slug = _slugify(candidate)
        if slug:
            return slug
    return "unknown-incident"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _split_title(title_text: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in title_text.split("|")]
    service = parts[0] if parts else "unknown"
    region = parts[1] if len(parts) > 1 else ""
    reference = parts[2] if len(parts) > 2 else ""
    return service, region, reference


def _extract_status(description: str) -> Optional[str]:
    match = re.search(r"<strong>([^<]+)</strong>", description, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _filter_incidents(incidents: List[Dict], targets: List[str]) -> List[Dict]:
    if not targets:
        return incidents

    target_set = {target.lower() for target in targets}
    filtered = []
    for incident in incidents:
        haystack = " ".join(
            [
                incident.get("title", ""),
                incident.get("region", ""),
                incident.get("service", ""),
            ]
        ).lower()
        if any(target in haystack for target in target_set):
            filtered.append(incident)
    return filtered


def get_monitor(slug: str = "oci") -> OciStatusMonitor:
    return OciStatusMonitor(slug=slug)
