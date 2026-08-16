import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ...core.config import ModuleConfig
from ...core.types import MonitorResult, MonitorStatus


class OpenAIStatusMonitor:
    def __init__(self, slug: str = "openai") -> None:
        self.id = slug
        self.config: Optional[ModuleConfig] = None

    def configure(self, config: ModuleConfig) -> None:
        self.config = config

    async def check(
        self, http_client: httpx.AsyncClient, logger: logging.Logger
    ) -> MonitorResult:
        if self.config is None:
            raise RuntimeError("openai monitor not configured")

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
                message="openai status request failed",
                reason=str(exc),
                duration_ms=round(duration_ms, 2),
            )

        duration_ms = (time.perf_counter() - start) * 1000

        rule_status, rule_reason, payload, reason_items = self._evaluate_rule(data)
        if rule_status == MonitorStatus.ERROR:
            return MonitorResult(
                status=MonitorStatus.ERROR,
                message="openai rule evaluation failed",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        if rule_status == MonitorStatus.ALERT:
            return MonitorResult(
                status=MonitorStatus.ALERT,
                message="openai status degraded",
                reason=rule_reason,
                duration_ms=round(duration_ms, 2),
                payload=payload,
                reason_items=reason_items,
            )

        return MonitorResult(
            status=MonitorStatus.OK,
            message="openai status healthy",
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
        targets = {item.strip().lower() for item in rule_value.split(",") if item.strip()}
        if not targets:
            targets = {"degraded_performance", "partial_outage", "major_outage"}

        components = _extract_components(data)
        if not components:
            return MonitorStatus.ERROR, "no components in status response", None, None

        filtered = components
        if self.config and self.config.service_filter:
            allow = {item.lower() for item in self.config.service_filter}
            filtered = [
                c
                for c in components
                if c["id"].lower() in allow or c["slug"] in allow or c["name"].lower() in allow
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


def _extract_components(data: Dict) -> List[Dict]:
    components = data.get("components") or []
    cleaned: List[Dict] = []
    for comp in components:
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


def get_monitor(slug: str = "openai") -> OpenAIStatusMonitor:
    return OpenAIStatusMonitor(slug=slug)

