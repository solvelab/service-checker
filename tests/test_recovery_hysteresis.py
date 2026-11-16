"""Monitoring-recovery hysteresis and the blind-window log (issue #87).

A notified monitoring failure used to end on the first successful check, and the
`pop` of its state also dropped `last_notified_at`. A flapping upstream therefore paged
"failure" and "restored" once per flap, and the repeat throttle never had a chance to
act: between 2026-11-15 10:07 and 2026-11-16 00:10 UTC the `steam` module, refused with
HTTP 403 on 45% of its checks, produced 51 failure and 47 recovery notifications.

The same logs carried 76 `monitor_blind_window` lines saying "no channel accepted the
notification" for error bursts of one or two checks, below the threshold of three — no
notification had been attempted at all.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.logging import JsonFormatter, configure_logging
from app.core.notifications import NotificationManager
from app.core.state_store import SCHEMA_VERSION, StateStore
from app.core.types import MonitorResult, MonitorStatus

#: Preso ao instante atual, não a uma data fixa: `StateStore.load()` sem `now` compara com o
#: relógio real, e um `_T0` fixo fazia `test_the_window_survives_a_restart` quebrar 24h depois
#: (#103). Mesmo motivo de `_T0` em `test_state_persistence.py`.
_T0 = datetime.now(timezone.utc).replace(microsecond=0)
_SEQUENCE = Path(__file__).parent / "fixtures" / "steam" / "check_statuses_2026-11-15.txt"


class Spy:
    def __init__(self, up=True):
        self.up = up
        self.events: list[str] = []

    async def _record(self, kind):
        self.events.append(kind)
        return self.up

    async def send_alert(self, **kwargs):
        return await self._record("alert")

    async def send_recovery(self, **kwargs):
        return await self._record("recovery")

    async def send_monitor_error(self, **kwargs):
        return await self._record("monitor_error")

    async def send_monitor_recovered(self, **kwargs):
        return await self._record("monitor_recovered")

    def count(self, kind):
        return self.events.count(kind)

    @staticmethod
    def with_reasons():
        spy = ReasonSpy()
        manager, _ = _manager(threshold=3, recovery=3, repeat=10, spy=spy)
        return manager, spy


class ReasonSpy(Spy):
    def __init__(self):
        super().__init__()
        self.reasons: list[str] = []

    async def send_monitor_error(self, **kwargs):
        self.reasons.append(kwargs["result"].reason or "")
        return await super().send_monitor_error(**kwargs)


def _manager(*, threshold=3, recovery=3, repeat=10, state_path=None, spy=None):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=repeat,
            error_threshold=threshold,
            recovery_threshold=recovery,
            state_path=state_path,
        )
    )
    spy = spy or Spy()
    manager.register("spy", spy)
    return manager, spy


def _module_config():
    return ModuleConfig("steam", "https://steamstat.us/", 60, 10.0, "ua",
                        RuleConfig("status", "major"), [], True)


async def _feed(manager, status, minute, payload=None, logger=None):
    reason = {"E": "UpstreamRejected: upstream returned HTTP 403"}.get(status)
    status = {"E": MonitorStatus.ERROR, "O": MonitorStatus.OK,
              "A": MonitorStatus.ALERT}.get(status, status)
    if payload is None:
        payload = [] if status == MonitorStatus.OK else None
    if status == MonitorStatus.ALERT and payload is None:
        payload = [{"id": "community", "name": "Steam Community", "status": "minor"}]
    await manager.handle_result(
        module_id="steam",
        result=MonitorResult(status, "m", reason, 10.0, payload),
        module_config=_module_config(),
        level_name="INFO",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=AsyncMock(),
        logger=logger or MagicMock(spec=logging.Logger),
    )


async def _run(manager, sequence, start=0):
    for offset, status in enumerate(sequence):
        await _feed(manager, status, start + offset)


def _capture():
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger, stream


def _events(stream, name):
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    return [line for line in lines if line.get("event") == name]


# ---------------------------------------------------------------------------
# The hysteresis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_flap_inside_a_notified_failure_pages_once_each_way():
    manager, spy = _manager(threshold=3, recovery=3)
    await _run(manager, "EEEOEEEOOO")

    assert spy.count("monitor_error") == 1
    assert spy.count("monitor_recovered") == 1


@pytest.mark.asyncio
async def test_recovery_needs_the_full_streak_of_successes():
    manager, spy = _manager(recovery=3)
    await _run(manager, "EEEOO")
    assert spy.count("monitor_recovered") == 0

    await _feed(manager, "O", 5)
    assert spy.count("monitor_recovered") == 1
    assert "steam" not in manager._error_state


@pytest.mark.asyncio
async def test_an_error_inside_the_window_restarts_the_streak():
    manager, spy = _manager(recovery=3)
    await _run(manager, "EEEOOEOO")

    assert spy.count("monitor_recovered") == 0
    assert manager._error_state["steam"].consecutive_successes == 2


@pytest.mark.asyncio
async def test_an_error_inside_the_window_respects_the_repeat_throttle():
    manager, spy = _manager(recovery=3, repeat=10)
    await _run(manager, "EEEOE")                     # notified at minute 2, error at 4
    assert spy.count("monitor_error") == 1

    await _run(manager, "EEOE", start=5)             # a full streak, but inside 10 min
    assert spy.count("monitor_error") == 1

    await _feed(manager, "E", 12)                    # past 10 min, but a lone error
    assert spy.count("monitor_error") == 1

    await _run(manager, "EE", start=13)              # past 10 min and sustained
    assert spy.count("monitor_error") == 2


@pytest.mark.asyncio
async def test_a_repage_after_a_flap_counts_only_the_current_streak():
    """The reason says "consecutive"; errors split by successes are not consecutive."""
    manager, spy = Spy.with_reasons()
    await _run(manager, "EEE" + "OE" * 8 + "EE")

    assert spy.count("monitor_error") == 2
    assert spy.reasons[-1].startswith("3 consecutive failed checks")


@pytest.mark.asyncio
async def test_a_refused_restored_message_is_retried_on_the_next_success():
    spy = Spy()
    manager, _ = _manager(threshold=3, recovery=3, spy=spy)
    await _run(manager, "EEEOO")
    spy.up = False
    await _feed(manager, "O", 5)                     # streak complete, nobody accepted
    assert spy.count("monitor_recovered") == 1
    assert "steam" in manager._error_state

    spy.up = True
    await _feed(manager, "O", 6)
    assert spy.count("monitor_recovered") == 2
    assert "steam" not in manager._error_state


@pytest.mark.asyncio
async def test_alert_counts_as_a_success_for_the_streak():
    manager, spy = _manager(recovery=2)
    await _run(manager, "EEEAA")
    assert spy.count("monitor_recovered") == 1


@pytest.mark.asyncio
async def test_threshold_one_keeps_the_previous_behaviour():
    manager, spy = _manager(threshold=3, recovery=1)
    await _run(manager, "EEEOEEEO")

    assert spy.count("monitor_error") == 2
    assert spy.count("monitor_recovered") == 2


@pytest.mark.asyncio
async def test_an_unannounced_streak_still_ends_on_the_first_success():
    manager, spy = _manager(threshold=3, recovery=3)
    await _run(manager, "EEO")

    assert "steam" not in manager._error_state
    await _run(manager, "EE", start=3)
    assert spy.count("monitor_error") == 0           # needs three in a row again


@pytest.mark.asyncio
async def test_service_recovery_does_not_wait_for_the_hysteresis():
    manager, spy = _manager(threshold=3, recovery=3)
    await _run(manager, "AEEE")
    assert spy.count("alert") == 1
    assert spy.count("monitor_error") == 1

    await _feed(manager, "O", 4)
    assert spy.count("recovery") == 1
    assert spy.count("monitor_recovered") == 0

    await _run(manager, "OO", start=5)
    assert spy.count("monitor_recovered") == 1


@pytest.mark.asyncio
async def test_recovery_threshold_comes_from_the_environment(monkeypatch):
    from app.core.config import load_app_config

    monkeypatch.setenv("NOTIFICATION_RECOVERY_THRESHOLD", "5")
    assert load_app_config().notifications.recovery_threshold == 5

    monkeypatch.setenv("NOTIFICATION_RECOVERY_THRESHOLD", "0")
    assert load_app_config().notifications.recovery_threshold == 1

    monkeypatch.delenv("NOTIFICATION_RECOVERY_THRESHOLD")
    assert load_app_config().notifications.recovery_threshold == 3


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_window_survives_a_restart(tmp_path):
    path = str(tmp_path / "state.json")
    before, _ = _manager(recovery=3, state_path=path)
    await _run(before, "EEEOO")

    after, spy = _manager(recovery=3, state_path=path)
    assert after._error_state["steam"].consecutive_successes == 2
    await _feed(after, "O", 5)
    assert spy.count("monitor_recovered") == 1


def test_state_without_the_success_count_loads_as_zero(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "errors": {"steam": {"consecutive_errors": 4,
                             "last_notified_at": _T0.isoformat()}},
    }), encoding="utf-8")
    _, errors = StateStore(str(path)).load(now=_T0)
    assert errors["steam"].consecutive_successes == 0


@pytest.mark.parametrize("raw", [-2, "3", None, 1.5, True])
def test_a_malformed_success_count_is_normalised(tmp_path, raw):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "errors": {"steam": {"consecutive_errors": 4, "consecutive_successes": raw}},
    }), encoding="utf-8")
    _, errors = StateStore(str(path)).load(now=_T0)
    assert errors["steam"].consecutive_successes == 0
    assert type(errors["steam"].consecutive_successes) is int


@pytest.mark.parametrize("offset", [timedelta(days=30), -timedelta(days=20)])
def test_an_error_entry_from_the_future_or_too_old_is_discarded(tmp_path, offset):
    """A future page would freeze the throttle; a 20-day-old one is a dead process."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "errors": {"steam": {"consecutive_errors": 4,
                             "last_notified_at": (_T0 + offset).isoformat()}},
    }), encoding="utf-8")
    _, errors = StateStore(str(path), max_age_minutes=1440).load(now=_T0)
    assert errors == {}


def test_a_recent_error_entry_is_kept(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "errors": {"steam": {"consecutive_errors": 4,
                             "last_notified_at": (_T0 - timedelta(minutes=30)).isoformat()}},
    }), encoding="utf-8")
    _, errors = StateStore(str(path), max_age_minutes=1440).load(now=_T0)
    assert errors["steam"].consecutive_errors == 4


# ---------------------------------------------------------------------------
# The blind window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_burst_below_the_threshold_is_not_a_blind_window():
    logger, stream = _capture()
    manager, spy = _manager(threshold=3)
    await _feed(manager, "E", 0, logger=logger)
    await _feed(manager, "E", 1, logger=logger)
    await _feed(manager, "O", 2, logger=logger)

    assert spy.events == []
    assert _events(stream, "monitor_blind_window") == []


@pytest.mark.asyncio
async def test_an_attempted_and_refused_failure_is_a_blind_window():
    logger, stream = _capture()
    spy = Spy(up=False)
    manager, _ = _manager(threshold=3, spy=spy)
    for minute in range(3):
        await _feed(manager, "E", minute, logger=logger)
    assert spy.count("monitor_error") == 1           # attempted, refused
    spy.up = True
    await _feed(manager, "O", 3, logger=logger)

    blind = _events(stream, "monitor_blind_window")
    assert len(blind) == 1
    assert "3 consecutive failed check(s)" in blind[0]["reason"]
    assert spy.count("monitor_recovered") == 0       # nobody heard of the failure


# ---------------------------------------------------------------------------
# Replay of the production sequence that motivated the change
# ---------------------------------------------------------------------------

def _production_sequence():
    lines = _SEQUENCE.read_text(encoding="utf-8").splitlines()
    return "".join(line for line in lines if not line.startswith("#"))


def test_the_production_sequence_is_intact():
    sequence = _production_sequence()
    assert len(sequence) == 814
    assert sequence.count("E") == 366
    assert set(sequence) <= {"E", "O", "A"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovery, failures, recoveries",
    [(1, 51, 47), (3, 37, 15)],
)
async def test_replaying_production_counts(recovery, failures, recoveries):
    """(1, 51, 47) is what production paged; (3, 37, 15) is the default from now on."""
    manager, spy = _manager(threshold=3, recovery=recovery, repeat=10)
    await _run(manager, _production_sequence())

    assert spy.count("monitor_error") == failures
    assert spy.count("monitor_recovered") == recoveries
