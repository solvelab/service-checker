import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...core.types import MonitorResult

_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(enabled_extensions=("j2", "html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)
_DEFAULT_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_alert.j2")
_STEAM_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_steam.j2")
_RESOLVED_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_resolved.j2")
_MONITOR_ERROR_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_monitor_error.j2")
_MONITOR_RECOVERED_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_monitor_recovered.j2")


class TelegramNotifier:
    def __init__(self, config) -> None:
        self.config = config

    async def send_alert(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_payload(module_id, payload, logger)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )

    async def send_recovery(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_with_template(_RESOLVED_TEMPLATE, payload, logger, module_id)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )


    async def send_monitor_error(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        await self._send_with_template(
            _MONITOR_ERROR_TEMPLATE,
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )

    async def send_monitor_recovered(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        await self._send_with_template(
            _MONITOR_RECOVERED_TEMPLATE,
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )

    async def _send_with_template(
        self,
        template,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_with_template(template, payload, logger, module_id)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )


def _build_payload(
    module_id: str,
    result: MonitorResult,
    interval_seconds: int,
    timestamp_format: str,
    timestamp_zone: str,
    level_name: str,
    event_name: str,
    event_time: datetime,
) -> dict:
    timestamp = _format_timestamp(event_time, timestamp_format, timestamp_zone)
    reason = result.reason or result.message or "sem detalhes"
    reason_items = _split_reason(reason)
    duration_ms = f"{result.duration_ms:.2f}" if result.duration_ms is not None else "0.00"
    message = result.message or "sem detalhes"
    return {
        "timestamp": timestamp,
        "level": level_name,
        "message": message,
        "event": event_name,
        "module_id": module_id,
        "status": result.status.value,
        "reason": reason,
        "reason_items": reason_items,
        "services": _serialize_services(result.payload),
        "duration_ms": duration_ms,
        "interval_seconds": interval_seconds,
    }


def _split_reason(reason: str) -> List[str]:
    parts = [item.strip() for item in reason.split(",")]
    return [item for item in parts if item]


def _serialize_services(payload) -> List[dict]:
    services: List[dict] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            services.append(
                {
                    "name": item.get("name") or item.get("id") or "service",
                    "status_text": item.get("status_text") or item.get("class") or "",
                    "severity": (item.get("severity") or "unknown").upper(),
                    "id": item.get("id") or "",
                    "slug": item.get("slug") or "",
                    "status": item.get("status") or "",
                    "from_status": item.get("from_status") or "",
                    "to_status": item.get("to_status") or "",
                }
            )
    return services


def _select_template(module_id: str):
    return _STEAM_TEMPLATE if module_id.lower() == "steam" else _DEFAULT_TEMPLATE


def _render_payload(module_id: str, payload: dict, logger: logging.Logger) -> str:
    return _render_with_template(_select_template(module_id), payload, logger, module_id)


def _render_with_template(
    template, payload: dict, logger: logging.Logger, module_id: str
) -> str:
    try:
        return template.render(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "telegram template render failed; using fallback",
            extra={
                "event": "notify_error",
                "module_id": module_id,
                "target": "telegram",
                "reason": str(exc),
            },
        )
        fallback = _DEFAULT_TEMPLATE
        try:
            return fallback.render(payload)
        except Exception:
            return f"{module_id} alert: {payload.get('message', 'no details')}"


def _format_timestamp(dt: datetime, fmt: str, zone: str) -> str:
    zone_upper = (zone or "UTC").strip().upper()
    if zone_upper == "LOCAL":
        target = dt.astimezone()
    else:
        target = dt.astimezone(timezone.utc)
    try:
        return target.strftime(fmt)
    except Exception:
        return target.isoformat()
