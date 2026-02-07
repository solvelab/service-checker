from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import ModuleConfig, NotificationConfig
from .types import MonitorResult, MonitorStatus
from ..notifications.telegram.notifier import TelegramNotifier
from ..notifications.webhook.notifier import WebhookNotifier


class NotificationManager:
    def __init__(self, config: NotificationConfig) -> None:
        self.config = config
        self.telegram_notifier: Optional[TelegramNotifier] = None
        self.webhook_notifier: Optional[WebhookNotifier] = None
        self._alert_state: dict[str, AlertState] = {}
        self._repeat_seconds = max(config.repeat_minutes, 1) * 60
        if config.telegram.enabled:
            self.telegram_notifier = TelegramNotifier(config.telegram)
        if config.webhook.enabled:
            self.webhook_notifier = WebhookNotifier(config.webhook)

    def has_notifiers(self) -> bool:
        return bool(self.telegram_notifier or self.webhook_notifier)

    async def handle_result(
        self,
        module_id: str,
        result: MonitorResult,
        module_config: ModuleConfig,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        service_items = _extract_service_items(result.payload)
        if service_items:
            await self._handle_service_result(
                module_id,
                result,
                service_items,
                module_config,
                level_name,
                event_name,
                event_time,
                http_client,
                logger,
            )
            return

        if result.status == MonitorStatus.OK:
            state = self._alert_state.get(module_id)
            if state is not None and state.last_status == MonitorStatus.ALERT:
                logger.info(
                    "recovery notification emitted",
                    extra={
                        "event": "monitor_recovery",
                        "module_id": module_id,
                        "check_id": module_id,
                        "from_status": state.last_status_text or "ALERT",
                        "to_status": "OK",
                    },
                )
                await self._notify_recovery(
                    module_id,
                    result,
                    module_config,
                    level_name="INFO",
                    event_name="monitor_resolved",
                    event_time=event_time,
                    http_client=http_client,
                    logger=logger,
                )
            self._alert_state[module_id] = AlertState(
                last_status=MonitorStatus.OK, last_alert_at=None
            )
            return

        if result.status != MonitorStatus.ALERT:
            self._alert_state[module_id] = AlertState(
                last_status=result.status, last_alert_at=None
            )
            return

        state = self._alert_state.get(module_id)
        event_time = _ensure_aware(event_time)
        should_send = False
        if state is None or state.last_status != MonitorStatus.ALERT:
            should_send = True
        elif state.last_alert_at is None:
            should_send = True
        elif (event_time - state.last_alert_at).total_seconds() >= self._repeat_seconds:
            should_send = True

        if not should_send:
            return

        await self._notify_alert(
            module_id,
            result,
            module_config,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )
        self._alert_state[module_id] = AlertState(
            last_status=MonitorStatus.ALERT,
            last_alert_at=event_time,
            last_status_text=result.reason or "ALERT",
        )

    async def _handle_service_result(
        self,
        module_id: str,
        result: MonitorResult,
        service_items: list[dict],
        module_config: ModuleConfig,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        event_time = _ensure_aware(event_time)
        if result.status == MonitorStatus.OK:
            for item in service_items:
                key = _service_key(module_id, item)
                state = self._alert_state.get(key)
                if state is not None and state.last_status == MonitorStatus.ALERT:
                    from_status = state.last_status_text or "ALERT"
                    enriched_item = {
                        **item,
                        "from_status": from_status,
                        "to_status": item.get("status") or "operational",
                    }
                    recovery_result = _build_service_result(
                        result, enriched_item, MonitorStatus.OK, "service restored"
                    )
                    logger.info(
                        "recovery notification emitted",
                        extra={
                            "event": "service_recovery",
                            "module_id": module_id,
                            "check_id": key,
                            "from_status": from_status,
                            "to_status": enriched_item["to_status"],
                        },
                    )
                    await self._notify_recovery(
                        module_id,
                        recovery_result,
                        module_config,
                        level_name="INFO",
                        event_name="service_resolved",
                        event_time=event_time,
                        http_client=http_client,
                        logger=logger,
                    )
                self._alert_state[key] = AlertState(
                    last_status=MonitorStatus.OK, last_alert_at=None
                )
            return

        if result.status != MonitorStatus.ALERT:
            for item in service_items:
                key = _service_key(module_id, item)
                self._alert_state[key] = AlertState(
                    last_status=result.status, last_alert_at=None
                )
            return

        for item in service_items:
            key = _service_key(module_id, item)
            state = self._alert_state.get(key)
            should_send = False
            if state is None or state.last_status != MonitorStatus.ALERT:
                should_send = True
            elif state.last_alert_at is None:
                should_send = True
            elif (event_time - state.last_alert_at).total_seconds() >= self._repeat_seconds:
                should_send = True

            if not should_send:
                continue

            alert_result = _build_service_result(
                result, item, MonitorStatus.ALERT, "service degraded"
            )
            await self._notify_alert(
                module_id,
                alert_result,
                module_config,
                level_name,
                "service_alert",
                event_time,
                http_client,
                logger,
            )
            self._alert_state[key] = AlertState(
                last_status=MonitorStatus.ALERT,
                last_alert_at=event_time,
                last_status_text=item.get("status") or item.get("severity") or item.get("status_text") or "ALERT",
            )

    async def _notify_alert(
        self,
        module_id: str,
        result: MonitorResult,
        module_config: ModuleConfig,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        if self.telegram_notifier:
            await self.telegram_notifier.send_alert(
                module_id=module_id,
                result=result,
                interval_seconds=module_config.interval_seconds,
                level_name=level_name,
                event_name=event_name,
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )
        if self.webhook_notifier:
            await self.webhook_notifier.send_alert(
                module_id=module_id,
                result=result,
                interval_seconds=module_config.interval_seconds,
                level_name=level_name,
                event_name=event_name,
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )

    async def _notify_recovery(
        self,
        module_id: str,
        result: MonitorResult,
        module_config: ModuleConfig,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        if self.telegram_notifier:
            await self.telegram_notifier.send_recovery(
                module_id=module_id,
                result=result,
                interval_seconds=module_config.interval_seconds,
                level_name=level_name,
                event_name=event_name,
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )
        if self.webhook_notifier:
            await self.webhook_notifier.send_recovery(
                module_id=module_id,
                result=result,
                interval_seconds=module_config.interval_seconds,
                level_name=level_name,
                event_name=event_name,
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )


@dataclass
class AlertState:
    last_status: MonitorStatus
    last_alert_at: Optional[datetime]
    last_status_text: Optional[str] = None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _extract_service_items(payload) -> list[dict]:
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    return []


def _service_key(module_id: str, item: dict) -> str:
    service_id = item.get("id") or item.get("slug") or item.get("name") or "service"
    return f"{module_id}:{service_id}".lower()


def _service_reason(item: dict) -> str:
    name = item.get("name") or item.get("id") or "service"
    status = item.get("status") or item.get("severity") or "unknown"
    status_text = item.get("status_text") or ""
    if status_text:
        return f"{name}: {status_text} ({status})"
    return f"{name}: {status}"


def _build_service_result(
    base: MonitorResult,
    item: dict,
    status: MonitorStatus,
    message: str,
) -> MonitorResult:
    return MonitorResult(
        status=status,
        message=message,
        reason=_service_reason(item),
        duration_ms=base.duration_ms,
        payload=[item],
    )
