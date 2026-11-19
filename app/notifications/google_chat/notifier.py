"""Google Chat incoming-webhook channel.

Messages are sent as `cardsV2` rather than as plain `text`. That is a safety choice,
not a cosmetic one: a card's `textParagraph` renders **HTML tags**, so dynamic content
is escaped with the same `html.escape` discipline the Telegram channel already uses.
The plain `text` field would instead need a bespoke escaper for Chat's own markup
(`*`, `_`, backtick, `<url|text>`) — a new escaper is new surface for defects.

Two constraints shape the rest of this module:

- A Chat space accepts **one request per second**, shared by every webhook in it.
  A provider with a dozen degraded components produces a dozen notifications in a
  single cycle, so sends are paced.
- The webhook URL is a **credential** — it carries `key` and `token` in the query
  string. It must never reach a log line, including error paths.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from ...core.reports import (
    STATUS_ICONS,
    SUBTITLES,
    TITLES,
    format_uptime,
    local_time,
    short_reason,
    status_label,
    summary_counts,
)
from ...core.types import MonitorResult, StatusReport

# Header per event type, so the four are distinguishable without reading the body.
_HEADERS = {
    "alert": ("🚨 Service alert", "A monitored service is degraded"),
    "recovery": ("✅ Service recovered", "The service is operational again"),
    "monitor_error": (
        "🛑 Monitoring failure",
        "The checker cannot reach this provider — the service itself may be fine",
    ),
    "monitor_recovered": (
        "🔄 Monitoring restored",
        "Checks are running again",
    ),
}

_REPLY_OPTION = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"


class GoogleChatNotifier:
    def __init__(self, config) -> None:
        self.config = config
        self._last_sent_at: float = 0.0
        # Pacing and post happen under one lock: two sends started together used to
        # both pass the quota check before either moved `_last_sent_at`, and the space
        # answered 429 — the startup card races the first alerts (bug-hunter).
        self._quota_lock = asyncio.Lock()

    async def send_alert(self, **kwargs) -> bool:
        return await self._send("alert", **kwargs)

    async def send_recovery(self, **kwargs) -> bool:
        return await self._send("recovery", **kwargs)

    async def send_monitor_error(self, **kwargs) -> bool:
        return await self._send("monitor_error", **kwargs)

    async def send_monitor_recovered(self, **kwargs) -> bool:
        return await self._send("monitor_recovered", **kwargs)

    # ------------------------------------------------------------------

    async def _send(
        self,
        kind: str,
        *,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
        **_ignored,
    ) -> bool:
        if not self.config.webhook_url:
            logger.warning(
                "google chat notifier missing webhook url; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "google_chat",
                },
            )
            return False

        body = self._build_message(kind, module_id, result, event_time, interval_seconds)
        url = self.config.webhook_url
        if self.config.thread_by_check:
            url = _with_reply_option(url)
        return await self._deliver(body, url, module_id, http_client, logger)

    async def send_status_report(
        self,
        *,
        report: StatusReport,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
        **_ignored,
    ) -> bool:
        """Startup announcement or daily heartbeat (#124), as a card of its own.

        No thread key: a report is not an incident, and grouping every heartbeat into
        one conversation would bury the day's in an old thread nobody opens.
        """
        if not self.config.webhook_url:
            logger.warning(
                "google chat notifier missing webhook url; skipping",
                extra={"event": "notify_skip", "target": "google_chat"},
            )
            return False
        body = _build_report_message(report)
        return await self._deliver(
            body, self.config.webhook_url, None, http_client, logger
        )

    async def _deliver(
        self,
        body: Dict[str, Any],
        url: str,
        module_id: Optional[str],
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        async with self._quota_lock:
            await self._respect_quota()
            try:
                response = await http_client.post(url, json=body, timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "google chat notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "google_chat",
                        "space": _space_id(self.config.webhook_url),
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
                return False
            self._last_sent_at = time.monotonic()

        status = getattr(response, "status_code", 0)
        if status >= 400:
            # 429 is not retried: pacing is the prevention, and retrying inside an
            # already rate-limited channel only stacks delay onto the next event.
            logger.error(
                "google chat notification rejected",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "google_chat",
                    "space": _space_id(self.config.webhook_url),
                    "reason": f"status {status}: {_body_text(response)}",
                },
            )
            return False

        logger.info(
            "google chat notification sent",
            extra={
                "event": "notify",
                "module_id": module_id,
                "target": "google_chat",
                "space": _space_id(self.config.webhook_url),
            },
        )
        return True

    async def _respect_quota(self) -> None:
        """Keep at least `min_interval_seconds` between two sends to this space."""
        interval = self.config.min_interval_seconds
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_sent_at
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)

    def _build_message(
        self,
        kind: str,
        module_id: str,
        result: MonitorResult,
        event_time: datetime,
        interval_seconds: int,
    ) -> Dict[str, Any]:
        title, subtitle = _HEADERS[kind]
        widgets: List[Dict[str, Any]] = [
            {
                "decoratedText": {
                    "topLabel": "Module",
                    "text": _esc(module_id),
                }
            }
        ]

        for item in _items(result):
            widgets.append({"textParagraph": {"text": f"• {_esc(item)}"}})

        if result.message:
            widgets.append(
                {"decoratedText": {"topLabel": "Message", "text": _esc(result.message)}}
            )
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": "When",
                    "text": _esc(event_time.isoformat()),
                }
            }
        )
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": "Check interval",
                    "text": f"{interval_seconds}s",
                }
            }
        )

        message: Dict[str, Any] = {
            "cardsV2": [
                {
                    "cardId": f"service-checker-{_slug(module_id)}-{kind}",
                    "card": {
                        "header": {"title": title, "subtitle": _esc(subtitle)},
                        "sections": [{"widgets": widgets}],
                    },
                }
            ]
        }

        if self.config.thread_by_check:
            message["thread"] = {"threadKey": _thread_key(module_id, result)}
        return message


def _build_report_message(report: StatusReport) -> Dict[str, Any]:
    facts = [
        ("Version", report.version),
        ("Pod", report.pod),
        ("Channels", ", ".join(report.channels) or "none"),
    ]
    if report.kind == "heartbeat":
        facts.append(("Uptime", format_uptime(report.sent_at - report.started_at)))
    facts.append(("Providers", summary_counts(report)))
    header_widgets = [
        {"decoratedText": {"topLabel": label, "text": _esc(value)}} for label, value in facts
    ]
    provider_widgets = []
    for line in report.modules:
        text = f"{STATUS_ICONS.get(line.status, '⚪')} <b>{_esc(line.module_id)}</b> — {_esc(status_label(line))}"
        if line.status is not None and line.status.value != "OK" and line.reason:
            text += f"<br>{_esc(short_reason(line.reason))}"
        if line.since is not None and report.kind == "heartbeat":
            text += f"<br><i>since {_esc(local_time(line.since, report.zone))}</i>"
        provider_widgets.append({"textParagraph": {"text": text}})
    return {
        "cardsV2": [
            {
                "cardId": f"service-checker-{report.kind}",
                "card": {
                    "header": {
                        "title": TITLES[report.kind],
                        "subtitle": _esc(SUBTITLES[report.kind]),
                    },
                    "sections": [
                        {"widgets": header_widgets},
                        {"header": "Providers", "widgets": provider_widgets
                         or [{"textParagraph": {"text": "No provider configured"}}]},
                    ],
                },
            }
        ]
    }


def _items(result: MonitorResult) -> List[str]:
    """One entry per incident, as the monitor separated them.

    Never re-split `reason`: no separator is safe across providers.
    """
    if result.reason_items:
        return [item for item in result.reason_items if item and item.strip()]
    if result.reason:
        return [result.reason]
    return []


def _thread_key(module_id: str, result: MonitorResult) -> str:
    """Stable per-component key, so an alert and its recovery share a conversation.

    Mirrors the alert-state key: the component id when the payload carries one,
    otherwise the module.
    """
    payload = result.payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        item = payload[0]
        component = item.get("id") or item.get("slug") or item.get("name")
        if component:
            return f"{_slug(module_id)}:{_slug(component)}"
    return _slug(module_id)


def _with_reply_option(url: str) -> str:
    if "messageReplyOption=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}messageReplyOption={_REPLY_OPTION}"


def _space_id(url: Optional[str]) -> str:
    """Identify the space for logs without ever echoing the credential.

    The URL carries `key` and `token` in its query string, so the path is the only
    part that is safe to record.
    """
    if not url:
        return "unknown"
    parts = [segment for segment in urlsplit(url).path.split("/") if segment]
    if "spaces" in parts:
        index = parts.index("spaces")
        if index + 1 < len(parts):
            return parts[index + 1]
    return "unknown"


def _body_text(response) -> str:
    text = getattr(response, "text", "") or ""
    return text[:200]


def _slug(value: str) -> str:
    """Reduce a value to an identifier safe for `cardId` and `threadKey`.

    These two are keys, not display text, so HTML escaping is the wrong tool here.
    The component half of a thread key comes from the provider payload, which is
    untrusted input — it must not carry arbitrary characters into a Chat key.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "unknown"


def _esc(value: str) -> str:
    """Escape for a card text field, which renders HTML tags."""
    return html.escape(str(value), quote=False)


def get_notifier(config) -> GoogleChatNotifier:
    return GoogleChatNotifier(config)
