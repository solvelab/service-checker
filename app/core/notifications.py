from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import ModuleConfig, NotificationConfig
from .state_store import StateStore
from .types import NOTIFIER_METHODS, MonitorResult, MonitorStatus, Notifier
from ..notifications.alertmanager.notifier import AlertmanagerNotifier
from ..notifications.google_chat.notifier import GoogleChatNotifier
from ..notifications.telegram.notifier import TelegramNotifier
from ..notifications.webhook.notifier import WebhookNotifier


class NotificationManager:
    def __init__(self, config: NotificationConfig) -> None:
        self.config = config
        self._notifiers: dict[str, Notifier] = {}
        self._alert_state: dict[str, AlertState] = {}
        self._error_state: dict[str, MonitorErrorState] = {}
        self._repeat_seconds = max(config.repeat_minutes, 1) * 60
        self._error_threshold = max(getattr(config, "error_threshold", 3), 1)
        # Pending alerts survive a restart when a path is configured. Without one this
        # is a no-op and the manager behaves exactly as it always did.
        self._store = StateStore(
            getattr(config, "state_path", None),
            getattr(config, "state_max_age_minutes", 1440),
        )
        if self._store.enabled:
            self._alert_state, self._error_state = self._store.load()
        if config.telegram.enabled:
            self.register("telegram", TelegramNotifier(config.telegram))
        if config.webhook.enabled:
            self.register("webhook", WebhookNotifier(config.webhook))
        google_chat = getattr(config, "google_chat", None)
        if google_chat is not None and google_chat.enabled:
            self.register("google_chat", GoogleChatNotifier(google_chat))
        alertmanager = getattr(config, "alertmanager", None)
        if alertmanager is not None and alertmanager.enabled:
            self.register("alertmanager", AlertmanagerNotifier(alertmanager))

    def register(self, name: str, notifier: Notifier) -> None:
        """Add a channel to the dispatch set.

        The four methods are checked here rather than trusted: a channel missing
        `send_monitor_recovered` used to look healthy until the first monitoring
        outage recovered in production, which is the worst possible moment to find out.
        """
        missing = [
            method
            for method in NOTIFIER_METHODS
            if not callable(getattr(notifier, method, None))
        ]
        if missing:
            raise TypeError(
                f"notifier '{name}' does not implement: {', '.join(missing)}"
            )
        self._notifiers[name] = notifier

    def unregister(self, name: str) -> None:
        self._notifiers.pop(name, None)

    def has_notifiers(self) -> bool:
        return bool(self._notifiers)

    # -- Compatibility shim -------------------------------------------------
    # The existing suites reach for these attributes to install spies, both
    # reading and assigning. Keeping them as views over the registry avoids
    # churning ~64 assertions that guard the alert-state invariants, in the very
    # change that puts those invariants at risk. Dispatch itself no longer names
    # any channel; drop these once the tests move to `register()`.

    @property
    def telegram_notifier(self) -> Optional[Notifier]:
        return self._notifiers.get("telegram")

    @telegram_notifier.setter
    def telegram_notifier(self, notifier: Optional[Notifier]) -> None:
        self._set_channel("telegram", notifier)

    @property
    def webhook_notifier(self) -> Optional[Notifier]:
        return self._notifiers.get("webhook")

    @webhook_notifier.setter
    def webhook_notifier(self, notifier: Optional[Notifier]) -> None:
        self._set_channel("webhook", notifier)

    def _set_channel(self, name: str, notifier: Optional[Notifier]) -> None:
        if notifier is None:
            self.unregister(name)
        else:
            self._notifiers[name] = notifier

    # -----------------------------------------------------------------------

    async def _dispatch(self, method: str, **kwargs) -> None:
        """Deliver one event to every registered channel.

        Each channel is isolated: an exception escaping one used to abort
        `handle_result` entirely, so a Telegram bot with a bad token could stop the
        webhook from ever seeing the alert.

        `logger` travels inside kwargs because every channel takes it; it is read
        back out here rather than passed separately, so the two cannot disagree.
        """
        logger: logging.Logger = kwargs["logger"]
        # Sem canal registrado nao ha o que entregar, e o estado deve avancar como
        # sempre avancou. So "havia canal e todos falharam" conta como nao entregue.
        delivered = not self._notifiers
        for name, notifier in list(self._notifiers.items()):
            try:
                await getattr(notifier, method)(**kwargs)
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "notification channel failed",
                    extra={
                        "event": "notify_error",
                        "module_id": kwargs.get("module_id"),
                        "target": name,
                        "reason": f"{method}: {type(exc).__name__}: {exc}",
                    },
                )
        return delivered

    async def handle_result(self, **kwargs) -> None:
        """Handle one monitor result, then persist whatever the state became.

        A thin wrapper on purpose: `_handle_result` returns from several places, and a
        flush written at each of them is a flush the next edit forgets. Persisting here
        means every path is covered by construction.
        """
        try:
            await self._handle_result(**kwargs)
        finally:
            if self._store.enabled:
                self._store.save(self._alert_state, self._error_state)

    async def _handle_result(
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

        # Reconcile before dispatching, and for ALERT as much as for OK: a component
        # can drop out of the payload while others are still degraded, and that is a
        # recovery too. This must not be gated on the branch below, because the whole
        # defect was that an emptied payload never reaches it.
        await self._recover_vanished_services(
            module_id, result, service_items, module_config, event_time, http_client, logger
        )

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

        delivered = await self._notify_alert(
            module_id,
            result,
            module_config,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )
        # Estado so avanca se alguem recebeu. Antes ele avancava sempre, e uma queda
        # de **um** ciclo no canal engolia o alerta pelos dez minutos do throttle — a
        # degradacao podia terminar antes, e ninguem jamais soube dela.
        if not delivered:
            return
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
        delivered = await self._dispatch(
            "send_monitor_error",
            module_id=module_id,
            result=failure_result,
            interval_seconds=module_config.interval_seconds,
            level_name="ERROR",
            event_name="monitor_failure",
            event_time=event_time,
            http_client=http_client,
            logger=logger,
        )
        if not delivered:
            # Nao anunciada e nao registrada: o ciclo seguinte tenta de novo, e a
            # recuperacao nao sera anunciada para um problema que ninguem soube.
            return
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
        if state is not None and state.consecutive_errors and state.last_notified_at is None:
            # Houve uma sequencia de falhas que nunca conseguiu ser anunciada — tipico
            # de perda de conectividade, em que o monitor e o canal caem juntos. Nao ha
            # o que notificar: o problema ja acabou, e anunciar a resolucao de algo que
            # ninguem soube foi justamente o defeito corrigido aqui. Mas ficar calado
            # sobre uma cegueira de varios ciclos e o padrao que este repositorio passou
            # o dia caçando, entao pelo menos o log registra.
            logger.warning(
                "monitor recovered from a failure streak that was never delivered",
                extra={
                    "event": "monitor_blind_window",
                    "module_id": module_id,
                    "reason": (
                        f"{state.consecutive_errors} consecutive failed check(s), "
                        f"no channel accepted the notification; last error: "
                        f"{state.last_reason or 'unknown'}"
                    ),
                },
            )
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
        await self._dispatch(
            "send_monitor_recovered",
            module_id=module_id,
            result=recovered_result,
            interval_seconds=module_config.interval_seconds,
            level_name="INFO",
            event_name="monitor_failure_resolved",
            event_time=event_time,
            http_client=http_client,
            logger=logger,
        )

    async def _recover_vanished_services(
        self,
        module_id: str,
        result: MonitorResult,
        service_items: list[dict],
        module_config: ModuleConfig,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        """Emit the all-clear for components that dropped out of the payload.

        A provider whose feed lists only *open* incidents says a component recovered by
        omitting it, never by publishing it as healthy. `aws` and `gcp` do exactly that,
        and their healthy payload is therefore an empty list — which is falsy, so the
        whole result used to route to the per-module branch and look up `<module_id>`,
        a key the per-service alert had never written. The alert fired on every channel
        and the all-clear could not fire at all: the operator was told AWS Direct Connect
        was degraded and was never told it was over.

        Presence is the wrong thing to reconcile against. The set of items in this cycle
        is the right one, and it is right whatever the overall status: three open events
        dropping to two is a recovery for the third even though the module still reports
        ALERT.
        """
        prefix = f"{module_id}:"
        present = {_service_key(module_id, item) for item in service_items}
        vanished = [
            key
            for key, state in self._alert_state.items()
            if key.startswith(prefix)
            and key not in present
            and state.last_status == MonitorStatus.ALERT
        ]

        for key in vanished:
            # Popped, not set to OK: the component is gone from the feed, so keeping a
            # key for it would grow the map for the process's whole life. Absence and
            # a recorded OK are equivalent to every reader of this state.
            state = self._alert_state.pop(key)
            from_status = state.last_status_text or "ALERT"
            item = state.last_item or {"name": key.split(":", 1)[1]}
            enriched_item = {
                **item,
                "from_status": from_status,
                "to_status": "operational",
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
                    "to_status": "operational",
                },
            )
            await self._notify_recovery(
                module_id,
                recovery_result,
                module_config,
                level_name="INFO",
                event_name="service_resolved",
                event_time=_ensure_aware(event_time),
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
                # Dropped rather than recorded as OK, for the same reason as in
                # `_recover_vanished_services`: a healthy component needs no bookkeeping,
                # and `state is None` reads identically to `last_status == OK` everywhere
                # this map is consulted.
                self._alert_state.pop(key, None)
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
            delivered = await self._notify_alert(
                module_id,
                alert_result,
                module_config,
                level_name,
                "service_alert",
                event_time,
                http_client,
                logger,
            )
            # Mesmo motivo do ramo por modulo: sem entrega, o proximo ciclo tenta de
            # novo em vez de o throttle suprimir um alerta que ninguem viu.
            if not delivered:
                continue
            self._alert_state[key] = AlertState(
                last_status=MonitorStatus.ALERT,
                last_alert_at=event_time,
                last_status_text=item.get("status") or item.get("severity") or item.get("status_text") or "ALERT",
                last_item=item,
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
    ) -> bool:
        return await self._dispatch(
            "send_alert",
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
    ) -> bool:
        return await self._dispatch(
            "send_recovery",
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
    # The component as it looked when it alerted. A provider that publishes only open
    # incidents announces a recovery by *omitting* the component, so by the time the
    # all-clear is due there is no item left to describe it with.
    last_item: Optional[dict] = None


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
    reason = _service_reason(item)
    return MonitorResult(
        status=status,
        message=message,
        reason=reason,
        # One component per notification here, so the bullet list is exactly this
        # one entry — never the comma-split of a name that may contain commas.
        reason_items=[reason],
        duration_ms=base.duration_ms,
        payload=[item],
    )
