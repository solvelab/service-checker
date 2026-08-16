from __future__ import annotations

import hashlib
import json
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
        self._error_state: dict[str, MonitorErrorState] = {}
        self._repeat_seconds = max(config.repeat_minutes, 1) * 60
        self._error_threshold = max(getattr(config, "error_threshold", 3), 1)
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
        event_time = _ensure_aware(event_time)

        # A failure to evaluate is about the monitor, not about any single component,
        # so it is handled per module regardless of what the payload looks like.
        if result.status == MonitorStatus.ERROR:
            await self._handle_monitor_error(
                module_id, result, module_config, event_time, http_client, logger
            )
            return

        # Any successful evaluation ends a monitoring outage, whatever it concluded.
        await self._clear_monitor_error(
            module_id, result, module_config, event_time, http_client, logger
        )

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

        state = self._alert_state.get(module_id)
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

    async def _handle_monitor_error(
        self,
        module_id: str,
        result: MonitorResult,
        module_config: ModuleConfig,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        """Count a failed evaluation and page once it stops looking transient.

        Never touches ``_alert_state``: a pending ALERT must survive an outage of the
        upstream, so that the eventual OK is still a transition and the repeat window
        is not reset.
        """
        state = self._error_state.setdefault(module_id, MonitorErrorState())
        state.consecutive_errors += 1
        state.last_reason = result.reason or result.message

        if state.consecutive_errors < self._error_threshold:
            return

        if state.last_notified_at is not None:
            elapsed = (event_time - state.last_notified_at).total_seconds()
            if elapsed < self._repeat_seconds:
                return

        logger.warning(
            "monitoring failure notification emitted",
            extra={
                "event": "monitor_failure",
                "module_id": module_id,
                "check_id": module_id,
                "status": MonitorStatus.ERROR.value,
                "reason": state.last_reason,
            },
        )
        failure_result = MonitorResult(
            status=MonitorStatus.ERROR,
            message="monitoring failure",
            reason=(
                f"{state.consecutive_errors} consecutive failed checks; "
                f"last error: {state.last_reason or 'unknown'}"
            ),
            duration_ms=result.duration_ms,
        )
        if self.telegram_notifier:
            await self.telegram_notifier.send_monitor_error(
                module_id=module_id,
                result=failure_result,
                interval_seconds=module_config.interval_seconds,
                level_name="ERROR",
                event_name="monitor_failure",
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )
        if self.webhook_notifier:
            await self.webhook_notifier.send_monitor_error(
                module_id=module_id,
                result=failure_result,
                interval_seconds=module_config.interval_seconds,
                level_name="ERROR",
                event_name="monitor_failure",
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )
        state.last_notified_at = event_time

    async def _clear_monitor_error(
        self,
        module_id: str,
        result: MonitorResult,
        module_config: ModuleConfig,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        """End a monitoring outage; only announce the recovery if the failure was announced."""
        state = self._error_state.pop(module_id, None)
        if state is None or state.last_notified_at is None:
            return

        logger.info(
            "monitoring recovery notification emitted",
            extra={
                "event": "monitor_failure_resolved",
                "module_id": module_id,
                "check_id": module_id,
                "from_status": MonitorStatus.ERROR.value,
                "to_status": result.status.value,
            },
        )
        recovered_result = MonitorResult(
            status=result.status,
            message="monitoring restored",
            reason=(
                f"upstream reachable again after {state.consecutive_errors} "
                f"failed check{'s' if state.consecutive_errors != 1 else ''}"
            ),
            duration_ms=result.duration_ms,
        )
        if self.telegram_notifier:
            await self.telegram_notifier.send_monitor_recovered(
                module_id=module_id,
                result=recovered_result,
                interval_seconds=module_config.interval_seconds,
                level_name="INFO",
                event_name="monitor_failure_resolved",
                event_time=event_time,
                http_client=http_client,
                logger=logger,
            )
        if self.webhook_notifier:
            await self.webhook_notifier.send_monitor_recovered(
                module_id=module_id,
                result=recovered_result,
                interval_seconds=module_config.interval_seconds,
                level_name="INFO",
                event_name="monitor_failure_resolved",
                event_time=event_time,
                http_client=http_client,
                logger=logger,
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
                key = _service_key(module_id, item, logger)
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

        # Only OK and ALERT reach here: handle_result intercepts ERROR before dispatching,
        # because a failure to evaluate is about the monitor, not about a component.
        for item in service_items:
            key = _service_key(module_id, item, logger)
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


@dataclass
class MonitorErrorState:
    """Monitoring-failure bookkeeping, kept in its own map.

    Deliberately not a field of AlertState: an ERROR must never disturb the
    pending-alert state, and this is keyed per module while AlertState is also
    keyed per component (``module:component``).
    """

    consecutive_errors: int = 0
    last_notified_at: Optional[datetime] = None
    last_reason: Optional[str] = None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _extract_service_items(payload) -> list[dict]:
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    return []


def _service_key(module_id: str, item: dict, logger: Optional[logging.Logger] = None) -> str:
    """Alert-state key for one component.

    Must be stable across cycles for the same component and distinct for different
    ones: a repeated key re-uses another component's throttle window and swallows its
    alert, while an unstable key re-alerts every cycle.

    A module that ships no usable identifier used to land on the literal ``"service"``,
    silently collapsing every one of its components onto a single key. The content
    digest below keeps them apart and the warning makes the gap visible.
    """
    service_id = item.get("id") or item.get("slug") or item.get("name")
    if not service_id:
        service_id = _content_digest(item)
        if logger is not None:
            logger.warning(
                "component has no id/slug/name; falling back to a content digest",
                extra={
                    "event": "service_key_fallback",
                    "module_id": module_id,
                    "check_id": f"{module_id}:{service_id}",
                    "reason": f"available keys: {sorted(item)}",
                },
            )
    return f"{module_id}:{service_id}".lower()


def _content_digest(item: dict) -> str:
    """Deterministic key derived from the item's own content.

    Stable across cycles (same content, same digest) and distinct across different
    content. Two byte-identical items in one payload still collapse — they are
    indistinguishable anyway, and a positional index would break stability whenever
    the upstream reorders its list.
    """
    canonical = json.dumps(item, sort_keys=True, default=str, ensure_ascii=True)
    return "sha-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


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
