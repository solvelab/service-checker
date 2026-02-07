import logging
from datetime import datetime

import httpx

from ...core.types import MonitorResult


class WebhookNotifier:
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
        if not self.config.url:
            logger.warning(
                "webhook notifier missing URL; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
            return

        headers = {}
        if self.config.token:
            headers[self.config.header_name] = self.config.token

        payload = {
            "timestamp": event_time.isoformat(),
            "level": level_name,
            "event": event_name,
            "module": module_id,
            "status": "ALERT",
            "message": result.message,
            "reason": result.reason,
            "payload": result.payload,
            "interval_seconds": interval_seconds,
        }

        try:
            await http_client.post(
                self.config.url,
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            logger.info(
                "webhook notification sent",
                extra={
                    "event": "notify",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "webhook notification failed",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "webhook",
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
        if not self.config.url:
            logger.warning(
                "webhook notifier missing URL; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
            return

        headers = {}
        if self.config.token:
            headers[self.config.header_name] = self.config.token

        check_id = module_id
        if isinstance(result.payload, list) and result.payload:
            first = result.payload[0]
            if isinstance(first, dict):
                sid = first.get("id") or first.get("slug") or first.get("name") or "service"
                check_id = f"{module_id}:{sid}".lower()

        payload = {
            "timestamp": event_time.isoformat(),
            "level": level_name,
            "event": event_name,
            "module": module_id,
            "check_id": check_id,
            "status": "RESOLVED",
            "message": result.message,
            "reason": result.reason,
            "payload": result.payload,
            "interval_seconds": interval_seconds,
        }

        try:
            await http_client.post(
                self.config.url,
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            logger.info(
                "webhook notification sent",
                extra={
                    "event": "notify",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "webhook notification failed",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "webhook",
                    "reason": str(exc),
                },
            )
