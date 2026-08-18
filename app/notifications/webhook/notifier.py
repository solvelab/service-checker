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
    ) -> bool:
        if not self.config.url:
            logger.warning(
                "webhook notifier missing URL; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
            return False

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

        return await self._deliver(payload, headers, module_id, http_client, logger)

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
    ) -> bool:
        if not self.config.url:
            logger.warning(
                "webhook notifier missing URL; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
            return False

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

        return await self._deliver(payload, headers, module_id, http_client, logger)

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
    ) -> bool:
        return await self._post(
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            "MONITOR_ERROR",
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
    ) -> bool:
        return await self._post(
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            "MONITOR_RECOVERED",
            http_client,
            logger,
        )

    async def _post(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        status: str,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if not self.config.url:
            logger.warning(
                "webhook notifier missing URL; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "webhook",
                },
            )
            return False

        headers = {}
        if self.config.token:
            headers[self.config.header_name] = self.config.token

        payload = {
            "timestamp": event_time.isoformat(),
            "level": level_name,
            "event": event_name,
            "module": module_id,
            "check_id": module_id,
            "status": status,
            "message": result.message,
            "reason": result.reason,
            "payload": result.payload,
            "interval_seconds": interval_seconds,
        }

        return await self._deliver(payload, headers, module_id, http_client, logger)

    async def _deliver(
        self,
        payload: dict,
        headers: dict,
        module_id: str,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        """POST the payload and say whether the endpoint took it.

        The return value is the contract with NotificationManager: `False` keeps the
        alert state where it is, so the next cycle sends again instead of the throttle
        suppressing an alert nobody received. Before this existed the failure was
        logged and the state advanced anyway.

        A 4xx/5xx counts as not delivered. The old code never looked at the status at
        all: an endpoint answering 500 to every request was indistinguishable from one
        that worked.
        """
        try:
            response = await http_client.post(
                self.config.url,
                json=payload,
                headers=headers,
                timeout=10.0,
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
            return False

        status = getattr(response, "status_code", 0)
        if status >= 400:
            logger.error(
                "webhook notification rejected",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "webhook",
                    "reason": f"status {status}",
                },
            )
            return False

        logger.info(
            "webhook notification sent",
            extra={
                "event": "notify",
                "module_id": module_id,
                "target": "webhook",
            },
        )
        return True
