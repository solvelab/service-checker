from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

from .config import ModuleConfig, NotificationConfig
from .state_store import StateStore, _parse_dt
from .types import (
    NOTIFIER_METHODS,
    ModuleReport,
    MonitorResult,
    MonitorStatus,
    Notifier,
    StatusReport,
    StatusReporter,
)
from ..notifications.alertmanager.notifier import AlertmanagerNotifier
from ..notifications.google_chat.notifier import GoogleChatNotifier
from ..notifications.telegram.notifier import TelegramNotifier
from ..notifications.webhook.notifier import WebhookNotifier


#: The module id a connectivity loss of the checker is announced under (#106). The
#: channels already know how to show a monitoring failure, so the loss reuses that
#: contract instead of adding a fifth method to every channel.
CONNECTIVITY_MODULE_ID = "checker"

#: Below this many loaded modules "most of them failing at once" says nothing.
_CONNECTIVITY_MIN_MODULES = 3


class NotificationManager:
    def __init__(self, config: NotificationConfig, module_ids: Iterable[str] = ()) -> None:
        self.config = config
        # The loaded modules, so a failure can be weighed against all of them (#106).
        # Empty — every test that builds a bare manager — keeps the detection off.
        self._module_ids = frozenset(module_ids)
        self._connectivity_quorum = getattr(config, "connectivity_quorum", 0.7)
        self._connectivity_tolerance = timedelta(
            minutes=max(getattr(config, "connectivity_tolerance_minutes", 10), 0)
        )
        self._connectivity_windows = tuple(getattr(config, "connectivity_windows", ()))
        self._connectivity_zone = ZoneInfo(
            getattr(config, "connectivity_timezone", "America/Sao_Paulo")
        )
        self._connectivity: Optional[ConnectivityLoss] = None
        # A streak made only of anti-bot challenges waits this long before paging (#112).
        self._challenge_tolerance = timedelta(
            minutes=max(getattr(config, "challenge_tolerance_minutes", 60), 0)
        )
        # Modules finish their cycles concurrently; without this, every result that
        # arrives while an announcement is in flight announces it again (bug-hunter).
        self._connectivity_lock = asyncio.Lock()
        self._notifiers: dict[str, Notifier] = {}
        # The last result of each module, for the status reports (#124). Raw on
        # purpose: the report shows what each provider answered, before tolerance and
        # thresholds decide what to page.
        self._snapshots: dict[str, ModuleReport] = {}
        # Set once every loaded module has delivered its first result.
        self.first_round = asyncio.Event()
        # Set when a module's status changes, so the log's status board follows (#125).
        self.status_changed = asyncio.Event()
        if not self._module_ids:
            self.first_round.set()
        self._alert_state: dict[str, AlertState] = {}
        self._error_state: dict[str, MonitorErrorState] = {}
        self._repeat_seconds = max(config.repeat_minutes, 1) * 60
        self._error_threshold = max(getattr(config, "error_threshold", 3), 1)
        self._recovery_threshold = max(getattr(config, "recovery_threshold", 3), 1)
        # Pending alerts survive a restart when a path is configured. Without one this
        # is a no-op and the manager behaves exactly as it always did.
        self._store = StateStore(
            getattr(config, "state_path", None),
            getattr(config, "state_max_age_minutes", 1440),
        )
        if self._store.enabled:
            self._alert_state, self._error_state = self._store.load()
            self._connectivity = ConnectivityLoss.restore(
                self._store.sections.get("connectivity"),
                max_age_minutes=getattr(config, "state_max_age_minutes", 1440),
            )
        if config.telegram.enabled:
            telegram = TelegramNotifier(
                config.telegram,
                max_age_minutes=getattr(config, "state_max_age_minutes", 1440),
            )
            if self._store.enabled:
                telegram.restore_state(self._store.sections.get("telegram_summary"))
            self.register("telegram", telegram)
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

    async def _dispatch(self, method: str, *, only: Optional[tuple] = None, **kwargs) -> bool:
        """Deliver one event to every registered channel, and say if anyone got it.

        Each channel is isolated: an exception escaping one used to abort
        `handle_result` entirely, so a Telegram bot with a bad token could stop the
        webhook from ever seeing the alert.

        `logger` travels inside kwargs because every channel takes it; it is read
        back out here rather than passed separately, so the two cannot disagree.

        The return value used to be "no channel raised", which was always true: the
        `Notifier` contract tells channels to swallow transport failures, and all four
        obey. So a webhook answering 500 to every request counted as delivered, the
        alert state advanced, and the repeat throttle suppressed an alert nobody had
        received. Channels now report delivery, and this reads what they report.
        """
        logger: logging.Logger = kwargs["logger"]
        # Sem canal registrado nao ha o que entregar, e o estado deve avancar como
        # sempre avancou. So "havia canal e todos falharam" conta como nao entregue.
        delivered = not self._notifiers
        for name, notifier in list(self._notifiers.items()):
            if only is not None and name not in only:
                continue
            try:
                accepted = await getattr(notifier, method)(**kwargs)
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
                continue
            if accepted is True:
                delivered = True
                continue
            if accepted is not False:
                # Contrato quebrado. Tratar como entrega — que e o que o codigo antigo
                # fazia com todo mundo — reabriria o vao em silencio para o proximo
                # canal escrito fora do contrato.
                logger.error(
                    "notification channel broke the delivery contract",
                    extra={
                        "event": "notify_error",
                        "module_id": kwargs.get("module_id"),
                        "target": name,
                        "reason": (
                            f"{method}: expected bool, got "
                            f"{type(accepted).__name__}"
                        ),
                    },
                )
            # `False` ja foi registrado pelo proprio canal, com o motivo concreto.
        return delivered

    def observe(self, module_id: str, result: MonitorResult, event_time: datetime) -> None:
        """Record a module's latest result for the status reports (#124)."""
        at = _ensure_aware(event_time)
        previous = self._snapshots.get(module_id)
        since = previous.since if previous and previous.status == result.status else at
        if result.status == MonitorStatus.ERROR:
            last_error, last_error_at = result.reason, at
        elif previous is not None:
            last_error, last_error_at = previous.last_error, previous.last_error_at
        else:
            last_error, last_error_at = None, None
        self._snapshots[module_id] = ModuleReport(
            module_id=module_id,
            status=result.status,
            reason=result.reason,
            checked_at=at,
            since=since,
            last_error=last_error,
            last_error_at=last_error_at,
        )
        # A first result counts as a change: a module slower than the startup timeout
        # would otherwise show "—" on every board until its status flipped.
        if previous is None or previous.status != result.status:
            self.status_changed.set()
        if not self.first_round.is_set() and self._module_ids <= self._snapshots.keys():
            self.first_round.set()

    def module_reports(self, module_ids: Iterable[str]) -> tuple:
        """One line per module, in the given order; None status when not checked yet."""
        return tuple(
            self._snapshots.get(module_id)
            or ModuleReport(module_id=module_id, status=None)
            for module_id in module_ids
        )

    def open_alerts(self) -> int:
        """Service alerts announced and not yet recovered.

        `_alert_state` also keeps an OK entry per module once it recovered or started
        healthy; counting entries said "2 open alerts" on an all-green board. Entries
        restored from the state file for a module no longer loaded have no row on the
        board, so they are left out. Keys are `<module>` or `<module>:<component>`.
        """
        return sum(
            state.last_status == MonitorStatus.ALERT
            for key, state in self._alert_state.items()
            if key.split(":", 1)[0] in self._module_ids
        )

    def channel_names(self) -> tuple:
        return tuple(self._notifiers)

    def report_channels(self) -> tuple:
        """The channels that deliver status reports to a human (not Alertmanager)."""
        return tuple(
            name
            for name, notifier in self._notifiers.items()
            if isinstance(notifier, StatusReporter)
        )

    async def send_status_report(
        self, report: StatusReport, http_client, logger: logging.Logger
    ) -> bool:
        """Deliver a status report to the human channels; True when anyone got it."""
        channels = self.report_channels()
        if not channels:
            return False
        return await self._dispatch(
            "send_status_report",
            only=channels,
            report=report,
            http_client=http_client,
            logger=logger,
        )

    async def handle_result(self, **kwargs) -> None:
        """Handle one monitor result, then persist whatever the state became.

        A thin wrapper on purpose: `_handle_result` returns from several places, and a
        flush written at each of them is a flush the next edit forgets. Persisting here
        means every path is covered by construction.
        """
        try:
            self.observe(kwargs["module_id"], kwargs["result"], kwargs["event_time"])
            await self._handle_result(**kwargs)
            await self._sync_connectivity(
                _ensure_aware(kwargs["event_time"]),
                kwargs["module_config"],
                kwargs["http_client"],
                kwargs["logger"],
            )
            await self._flush_channels(kwargs["http_client"], kwargs["logger"])
        finally:
            if self._store.enabled:
                self._store.save(
                    self._alert_state, self._error_state, self._channel_sections()
                )

    async def _flush_channels(self, http_client, logger: logging.Logger) -> None:
        """Let a channel retry what it could not deliver (the Telegram summary, #100).

        A service recovery is never re-dispatched — its state is gone whatever the
        channels answered — so a summary edit refused once would stay stale all day.
        """
        flush = getattr(self._notifiers.get("telegram"), "flush", None)
        if not callable(flush):
            return
        try:
            await flush(http_client=http_client, logger=logger)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "notification channel flush failed",
                extra={
                    "event": "notify_error",
                    "target": "telegram",
                    "reason": f"flush: {type(exc).__name__}: {exc}",
                },
            )

    def _channel_sections(self) -> dict:
        """Opaque state kept across restarts: the Telegram summary (#100), the loss (#106)."""
        sections = {}
        export = getattr(self._notifiers.get("telegram"), "export_state", None)
        section = export() if callable(export) else None
        if isinstance(section, dict):
            sections["telegram_summary"] = section
        if self._connectivity is not None:
            sections["connectivity"] = self._connectivity.to_dict()
        return sections

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
                    replace(result, started_at=state.started_at,
                            page_url=_page_url(module_config)),
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

        ongoing = state is not None and state.last_status == MonitorStatus.ALERT
        opened_at = state.started_at if ongoing else event_time
        delivered = await self._notify_alert(
            module_id,
            replace(result, started_at=opened_at, page_url=_page_url(module_config)),
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
            opened_at=opened_at,
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
        if state.consecutive_successes:
            # An error inside the recovery window continues the same failure, but not
            # the same streak: the count restarts so "N consecutive failed checks" stays
            # true, and paging again needs a sustained streak *and* the repeat window —
            # `last_notified_at` is kept for that.
            state.consecutive_errors = 0
            state.consecutive_successes = 0
        if (
            state.consecutive_errors
            and state.challenge_only
            and not result.challenged
            # Still held: past the tolerance the page is due (and maybe already
            # refused), so the streak goes on and keeps its blind window (bug-hunter).
            and self._challenge_tolerated(state, event_time)
        ):
            # The challenges said nothing about the provider, so they must not count
            # toward this error's threshold: inherited, the first DNS error of a router
            # outage paged at once, before the connectivity loss could open (#114).
            self._end_challenge_streak(module_id, state, event_time, logger)
        state.consecutive_errors += 1
        state.last_reason = result.reason or result.message
        if state.consecutive_errors == 1 or state.first_error_at is None:
            state.first_error_at = event_time
            state.challenge_only = result.challenged
        elif not result.challenged:
            # One error that is not the edge's challenge makes an announced streak an
            # ordinary failure again (#112); a held one was restarted above (#114).
            state.challenge_only = False

        if state.consecutive_errors < self._error_threshold:
            return

        if self._connectivity_open() or self._connectivity_quorum_reached():
            # Most modules failing together is the checker's own network, not N
            # providers: the loss is announced once, by `_sync_connectivity`, and
            # this module's failure is not (#106).
            if not self._connectivity_open():
                self._open_connectivity(
                    event_time, state.last_reason, module_config.interval_seconds, logger
                )
            if state.consecutive_errors == self._error_threshold:
                # Reached the threshold inside the loss: nothing was ever attempted for
                # it. A module that crossed it earlier had its send attempted (and maybe
                # refused), so it keeps its blind-window log.
                state.connectivity_loss = True
            return

        if self._challenge_tolerated(state, event_time):
            # The source refused the robot, which says nothing about the provider: hold
            # the page until the challenge outlasts the tolerance (#112).
            state.challenge_absorbed = True
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
            # A held challenge streak is announced late, so it carries its real start
            # for a channel that dates the problem itself (the Telegram summary).
            payload=(
                [{"started_at": state.first_error_at.isoformat()}]
                if state.challenge_absorbed or self._challenge_announced_late(state, event_time)
                else None
            ),
        )
        # From here a send was attempted: a refusal is a blind window again.
        state.challenge_absorbed = False
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
        """End a monitoring outage; only announce the recovery if the failure was announced.

        A notified failure ends only after `recovery_threshold` successes in a row; until
        then the state stays, so an error inside the window is the same failure and is
        throttled as such. An unannounced streak still ends on the first success.
        """
        state = self._error_state.get(module_id)
        if state is not None and state.last_notified_at is not None:
            state.consecutive_successes += 1
            if state.consecutive_successes < self._recovery_threshold:
                return
            # Announced failure: the state goes only once someone receives the
            # "restored" message, like every other transition here (#71).
            await self._announce_monitor_recovered(
                module_id, state, result, module_config, event_time, http_client, logger
            )
            self._maybe_close_connectivity(event_time, logger)
            return
        state = self._error_state.pop(module_id, None)
        self._maybe_close_connectivity(event_time, logger)
        if (
            state is not None
            and state.consecutive_errors >= self._error_threshold
            and state.last_notified_at is None
            # Absorbed by a connectivity loss: nothing was attempted for this module,
            # the loss itself is what gets announced (#106).
            and not state.connectivity_loss
            # Held by the challenge tolerance: nothing was attempted either (#112).
            and not state.challenge_absorbed
        ):
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

        # Only a streak that is still challenge-only was held as one (bug-hunter).
        if state is not None and state.challenge_absorbed and state.challenge_only:
            self._log_challenge_absorbed(module_id, state, event_time, logger)

    def _end_challenge_streak(
        self,
        module_id: str,
        state: "MonitorErrorState",
        event_time: datetime,
        logger: logging.Logger,
    ) -> None:
        """Close a not-yet-announced challenge streak so the next error starts its own."""
        if state.challenge_absorbed:
            self._log_challenge_absorbed(module_id, state, event_time, logger)
        state.consecutive_errors = 0
        state.first_error_at = None
        state.challenge_only = False
        state.challenge_absorbed = False
        state.connectivity_loss = False

    @staticmethod
    def _log_challenge_absorbed(
        module_id: str,
        state: "MonitorErrorState",
        event_time: datetime,
        logger: logging.Logger,
    ) -> None:
        logger.info(
            "challenge streak ended below the tolerance",
            extra={
                "event": "monitor_challenge_absorbed",
                "module_id": module_id,
                "reason": (
                    f"{state.consecutive_errors} consecutive challenged check(s) from "
                    f"{state.first_error_at.isoformat() if state.first_error_at else 'unknown'} "
                    f"to {event_time.isoformat()}; last error: "
                    f"{state.last_reason or 'unknown'}"
                ),
            },
        )

    def _challenge_tolerated(self, state: "MonitorErrorState", event_time: datetime) -> bool:
        """A not-yet-announced streak of challenges only, still inside the tolerance."""
        if (
            state.last_notified_at is not None
            or not state.challenge_only
            or state.first_error_at is None
        ):
            return False
        held = event_time - state.first_error_at
        # A start in the future (restored file, clock stepped back) would hold the page
        # forever, even with the tolerance off: it pages instead (bug-hunter).
        return timedelta(0) <= held < self._challenge_tolerance

    def _challenge_announced_late(self, state: "MonitorErrorState", event_time: datetime) -> bool:
        """A first announcement of a challenge streak the tolerance held, retries included."""
        return (
            self._challenge_tolerance > timedelta(0)
            and state.last_notified_at is None
            and state.challenge_only
            and state.first_error_at is not None
            and event_time - state.first_error_at >= self._challenge_tolerance
        )

    # -- connectivity loss of the checker (#106) -------------------------------

    def _connectivity_enabled(self) -> bool:
        return len(self._module_ids) >= _CONNECTIVITY_MIN_MODULES

    def _connectivity_open(self) -> bool:
        return self._connectivity is not None and self._connectivity.ended_at is None

    def _failing_modules(self) -> list[str]:
        """Loaded modules whose latest evaluation failed (a recovery window does not count)."""
        return [
            module_id
            for module_id, state in self._error_state.items()
            if module_id in self._module_ids
            and state.consecutive_errors >= 1
            and state.consecutive_successes == 0
            # A challenge means the edge answered: it is no evidence that the checker's
            # network is down (#112, bug-hunter).
            and not state.challenge_only
        ]

    def _connectivity_quorum_reached(self) -> bool:
        if not self._connectivity_enabled():
            return False
        failing = len(self._failing_modules())
        return failing / len(self._module_ids) >= self._connectivity_quorum

    def _open_connectivity(
        self,
        event_time: datetime,
        reason: Optional[str],
        interval_seconds: int,
        logger: logging.Logger,
    ) -> None:
        failing = self._failing_modules()
        started_at = self._connectivity_start(failing, event_time, interval_seconds)
        previous = self._connectivity
        if previous is not None:
            # A loss that ended but whose all-clear was never delivered: the network
            # dropped again before anyone heard, so it is the same loss continuing.
            previous.ended_at = None
            previous.last_reason = reason
            previous.failing = len(failing)
            return
        self._connectivity = ConnectivityLoss(
            started_at=started_at,
            last_reason=reason,
            failing=len(failing),
            total=len(self._module_ids),
        )
        logger.warning(
            "checker lost connectivity",
            extra={
                "event": "connectivity_lost",
                "module_id": CONNECTIVITY_MODULE_ID,
                "reason": (
                    f"{len(failing)}/{len(self._module_ids)} modules failing since "
                    f"{started_at.isoformat()}; last error: {reason or 'unknown'}"
                ),
            },
        )

    def _connectivity_start(
        self, failing: list[str], event_time: datetime, interval_seconds: int
    ) -> datetime:
        """When the network went, judged by the modules that fell with it.

        The earliest error of *every* failing module is wrong: a provider down on its own
        for hours would date a 6-minute router reboot hours back, skip its window and
        page it (bug-hunter). The loss is anchored where the quorum-th module started
        failing, and only errors that began within one threshold's worth of checks
        before that count. A failure already announced on its own is a separate story.
        """
        starts = sorted(
            state.first_error_at
            for module_id in failing
            if (state := self._error_state[module_id]).first_error_at is not None
            and state.last_notified_at is None
        )
        if not starts:
            return event_time
        quorum_count = max(math.ceil(self._connectivity_quorum * len(self._module_ids)), 1)
        anchor = starts[min(quorum_count, len(starts)) - 1]
        lookback = timedelta(seconds=max(interval_seconds, 1) * self._error_threshold)
        return min(start for start in starts if start >= anchor - lookback)

    def _maybe_close_connectivity(self, event_time: datetime, logger: logging.Logger) -> None:
        """End the loss once the failing modules drop below the quorum."""
        if not self._connectivity_open() or self._connectivity_quorum_reached():
            return
        loss = self._connectivity
        loss.ended_at = event_time
        # Whoever is still failing goes back to the per-module flow and must reach the
        # threshold again from here: a provider that really went down together with
        # the network is still caught, without inheriting the outage's count.
        # A challenge-only streak is not among them (it never counted toward the loss),
        # so its tolerance clock keeps the real start (#112, bug-hunter).
        for module_id in self._failing_modules():
            state = self._error_state[module_id]
            state.consecutive_errors = 0
            state.first_error_at = None
        for state in self._error_state.values():
            state.connectivity_loss = False
        logger.info(
            "checker connectivity restored",
            extra={
                "event": "connectivity_restored",
                "module_id": CONNECTIVITY_MODULE_ID,
                "reason": (
                    f"down from {loss.started_at.isoformat()} to {event_time.isoformat()} "
                    f"({_format_duration(event_time - loss.started_at)}); "
                    f"last error: {loss.last_reason or 'unknown'}"
                ),
            },
        )

    def _connectivity_window_end(self, started_at: datetime) -> Optional[datetime]:
        """The end of the latest expected-loss window that contains `started_at`."""
        local = started_at.astimezone(self._connectivity_zone)
        minute = local.hour * 60 + local.minute
        ends = []
        for start, end in self._connectivity_windows:
            if start < end:
                inside = start <= minute < end
                day = local.date()
            else:  # crosses midnight, e.g. 23:50-00:10
                inside = minute >= start or minute < end
                day = local.date() + timedelta(days=1 if minute >= start else 0)
            if inside:
                ends.append(
                    datetime.combine(
                        day, time(end // 60, end % 60), tzinfo=self._connectivity_zone
                    )
                )
        return max(ends, default=None)

    def _connectivity_due(self, loss: "ConnectivityLoss", at: datetime) -> bool:
        """Whether a loss seen at `at` deserves a notification."""
        window_end = self._connectivity_window_end(loss.started_at)
        if window_end is not None and at <= window_end:
            return False
        return at - loss.started_at >= self._connectivity_tolerance

    async def _sync_connectivity(
        self,
        event_time: datetime,
        module_config: ModuleConfig,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        """Announce the loss once it is due, then its end; retried on every result.

        Like every other transition here, nothing advances until a channel accepts it,
        and the end is never sent before the failure it closes.
        """
        async with self._connectivity_lock:
            await self._sync_connectivity_locked(event_time, module_config, http_client, logger)

    async def _sync_connectivity_locked(
        self,
        event_time: datetime,
        module_config: ModuleConfig,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        loss = self._connectivity
        if loss is None:
            return
        if loss.ended_at is None:
            if loss.notified_at is None:
                if self._connectivity_due(loss, event_time):
                    await self._announce_connectivity_lost(
                        loss, module_config, event_time, http_client, logger
                    )
            elif (event_time - loss.notified_at).total_seconds() >= self._repeat_seconds:
                # Repeated like any open monitoring failure: Alertmanager expires a
                # firing alert that is not refreshed (bug-hunter), and the summary only
                # edits in silence.
                await self._announce_connectivity_lost(
                    loss, module_config, event_time, http_client, logger
                )
            return
        if loss.notified_at is None:
            if not self._connectivity_due(loss, loss.ended_at):
                # Short, or inside an expected window: the log already has it.
                self._connectivity = None
                return
            if not await self._announce_connectivity_lost(
                loss, module_config, event_time, http_client, logger
            ):
                return
        delivered = await self._dispatch(
            "send_monitor_recovered",
            module_id=CONNECTIVITY_MODULE_ID,
            result=MonitorResult(
                status=MonitorStatus.OK,
                message="monitoring restored",
                reason=(
                    f"checker connectivity restored after "
                    f"{_format_duration(loss.ended_at - loss.started_at)}; "
                    f"last error: {loss.last_reason or 'unknown'}"
                ),
            ),
            interval_seconds=module_config.interval_seconds,
            level_name="INFO",
            event_name="monitor_failure_resolved",
            event_time=event_time,
            http_client=http_client,
            logger=logger,
        )
        if not delivered or self._connectivity is not loss:
            return
        if loss.ended_at is None:
            # The network dropped again while the all-clear was in flight: what is open
            # now is a new loss, owed its own announcement.
            loss.started_at = event_time
            loss.notified_at = None
            return
        self._connectivity = None

    async def _announce_connectivity_lost(
        self,
        loss: "ConnectivityLoss",
        module_config: ModuleConfig,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        delivered = await self._dispatch(
            "send_monitor_error",
            module_id=CONNECTIVITY_MODULE_ID,
            result=MonitorResult(
                status=MonitorStatus.ERROR,
                message="monitoring failure",
                reason=(
                    f"checker lost connectivity: {loss.failing}/{loss.total} modules "
                    f"failing since {loss.started_at.isoformat()}; "
                    f"last error: {loss.last_reason or 'unknown'}"
                ),
                # The real start, for a channel that dates the problem itself (the
                # Telegram summary). `event_time` stays "now": a backdated one broke the
                # summary's resend cooldown and Alertmanager's endsAt (bug-hunter).
                payload=[{"started_at": loss.started_at.isoformat()}],
            ),
            interval_seconds=module_config.interval_seconds,
            level_name="ERROR",
            event_name="monitor_failure",
            event_time=event_time,
            http_client=http_client,
            logger=logger,
        )
        if delivered:
            loss.notified_at = event_time
        return delivered

    async def _announce_monitor_recovered(
        self,
        module_id: str,
        state: "MonitorErrorState",
        result: MonitorResult,
        module_config: ModuleConfig,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
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
                f"upstream reachable again for {state.consecutive_successes} "
                f"check{'s' if state.consecutive_successes != 1 else ''} in a row; "
                f"last error: {state.last_reason or 'unknown'}"
            ),
            duration_ms=result.duration_ms,
        )
        delivered = await self._dispatch(
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
        if delivered:
            self._error_state.pop(module_id, None)
        # Not delivered: the state stays with its success streak complete, so the next
        # successful check tries again instead of leaving the failure open forever.

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
                result, enriched_item, MonitorStatus.OK, "service restored",
                started_at=state.started_at, page_url=_page_url(module_config),
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
                        result, enriched_item, MonitorStatus.OK, "service restored",
                        started_at=state.started_at, page_url=_page_url(module_config),
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

            ongoing = state is not None and state.last_status == MonitorStatus.ALERT
            opened_at = state.started_at if ongoing else event_time
            alert_item = item
            status_text = _status_text(item)
            if ongoing and state.last_status_text and state.last_status_text != status_text:
                # A repeat whose state moved (partial_outage -> degraded_performance).
                alert_item = {**item, "from_status": state.last_status_text}
            alert_result = _build_service_result(
                result, alert_item, MonitorStatus.ALERT, "service degraded",
                started_at=opened_at, page_url=_page_url(module_config),
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
                last_status_text=status_text,
                # Incident titles describe this check only; a later vanished-path
                # recovery must not quote them as if still current.
                last_item={k: v for k, v in item.items() if k != "incidents"},
                opened_at=opened_at,
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
    # First delivered alert of this key, kept across repeats (#142). The start of the
    # problem as the channels saw it; `last_alert_at` is only the repeat throttle.
    opened_at: Optional[datetime] = None

    @property
    def started_at(self) -> Optional[datetime]:
        return self.opened_at or self.last_alert_at


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
    # Successes in a row since the last error, counted only while a notified failure
    # is open (the recovery hysteresis).
    consecutive_successes: int = 0
    # When the current streak started; the start of a connectivity loss (#106).
    first_error_at: Optional[datetime] = None
    # The streak reached the threshold during a connectivity loss of the checker, so
    # the loss stood in for this module's own failure notification.
    connectivity_loss: bool = False
    # Every error of the current streak was the edge's anti-bot challenge (#112).
    challenge_only: bool = False
    # The challenge tolerance held this streak's announcement: nothing was attempted.
    challenge_absorbed: bool = False


@dataclass
class ConnectivityLoss:
    """A connectivity loss of the checker itself (#106), open while `ended_at` is None."""

    started_at: datetime
    ended_at: Optional[datetime] = None
    # When the failure reached a channel; None while it is not due or not delivered.
    notified_at: Optional[datetime] = None
    last_reason: Optional[str] = None
    failing: int = 0
    total: int = 0

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "notified_at": self.notified_at.isoformat() if self.notified_at else None,
            "last_reason": self.last_reason,
            "failing": self.failing,
            "total": self.total,
        }

    @classmethod
    def restore(
        cls, data, now: Optional[datetime] = None, max_age_minutes: int = 1440
    ) -> Optional["ConnectivityLoss"]:
        """Rebuild from `to_dict`; anything unreadable or stale means no loss, never a crash.

        Stale like an alert: a loss last seen longer ago than `max_age_minutes` belongs to
        a process that was down, and restoring it paged "lost 3 days ago" (bug-hunter).
        """
        if not isinstance(data, dict):
            return None
        started_at = _parse_dt(data.get("started_at"))
        if started_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        last_seen = _parse_dt(data.get("ended_at")) or started_at
        if last_seen > now + timedelta(minutes=5) or (
            max_age_minutes and now - last_seen > timedelta(minutes=max_age_minutes)
        ):
            return None
        failing, total = data.get("failing"), data.get("total")
        return cls(
            started_at=started_at,
            ended_at=_parse_dt(data.get("ended_at")),
            notified_at=_parse_dt(data.get("notified_at")),
            last_reason=data.get("last_reason") if isinstance(data.get("last_reason"), str) else None,
            failing=failing if type(failing) is int and failing >= 0 else 0,
            total=total if type(total) is int and total >= 0 else 0,
        )


def _format_duration(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


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


def _status_text(item: dict) -> str:
    return item.get("status") or item.get("severity") or item.get("status_text") or "ALERT"


def _page_url(module_config: ModuleConfig) -> Optional[str]:
    """Scheme and host of the module URL: the provider's status page, for a link."""
    url = getattr(module_config, "url", None)
    if not isinstance(url, str) or "://" not in url:
        return None
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}" if host else None


def _build_service_result(
    base: MonitorResult,
    item: dict,
    status: MonitorStatus,
    message: str,
    *,
    started_at: Optional[datetime] = None,
    page_url: Optional[str] = None,
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
        started_at=started_at,
        page_url=page_url,
    )
