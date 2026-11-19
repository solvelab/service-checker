"""Connectivity loss of the checker itself (issue #106).

On 2026-11-16 the home router rebooted around 07:00Z: all 10 modules failed together for
five to six checks, each crossed the threshold and paged its own monitoring failure, and
the Telegram summary gained ten "04:02 -> 04:08" lines although no provider had a
problem. The replay below uses the start time, error count and recovery time of every
module from the production log of that morning.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
    drain_config_warnings,
    load_app_config,
)
from app.core.notifications import CONNECTIVITY_MODULE_ID, NotificationManager
from app.core.types import MonitorResult, MonitorStatus

pytestmark = pytest.mark.asyncio

_MODULES = (
    "aws", "bitbucket", "claude", "cloudflare", "gcp",
    "github", "oci", "openai", "rockstar", "steam",
)

#: (module, first error "HH:MM:SS" UTC, consecutive errors, first OK "HH:MM:SS" UTC)
_OUTAGE_2026_11_16 = (
    ("aws", "07:00:31", 5, "07:05:36"),
    ("bitbucket", "07:00:15", 6, "07:06:19"),
    ("claude", "07:01:07", 5, "07:06:18"),
    ("cloudflare", "07:00:17", 6, "07:06:22"),
    ("gcp", "07:00:19", 6, "07:06:25"),
    ("github", "07:01:06", 5, "07:06:18"),
    ("oci", "07:01:06", 5, "07:06:18"),
    ("openai", "07:00:19", 6, "07:06:25"),
    ("rockstar", "07:00:28", 5, "07:05:54"),
    ("steam", "07:01:16", 5, "07:06:36"),
)

_DAY = datetime(2026, 11, 16, tzinfo=timezone.utc)


class Spy:
    def __init__(self, up=True):
        self.up = up
        self.calls: list[tuple[str, str, datetime]] = []
        self.starts: list[str] = []

    async def _record(self, kind, kwargs):
        self.calls.append((kind, kwargs["module_id"], kwargs["event_time"]))
        payload = kwargs["result"].payload
        if kind == "monitor_error" and kwargs["module_id"] == CONNECTIVITY_MODULE_ID:
            self.starts.append(payload[0]["started_at"])
        return self.up

    async def send_alert(self, **kwargs):
        return await self._record("alert", kwargs)

    async def send_recovery(self, **kwargs):
        return await self._record("recovery", kwargs)

    async def send_monitor_error(self, **kwargs):
        return await self._record("monitor_error", kwargs)

    async def send_monitor_recovered(self, **kwargs):
        return await self._record("monitor_recovered", kwargs)

    def kinds(self, module_id=None):
        return [k for k, m, _ in self.calls if module_id is None or m == module_id]


class Events(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def count(self, event):
        return sum(1 for r in self.records if getattr(r, "event", None) == event)


def _manager(*, modules=_MODULES, tolerance=10, quorum=0.7, windows=(), state_path=None,
             spy=None):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
            recovery_threshold=3,
            state_path=state_path,
            connectivity_quorum=quorum,
            connectivity_tolerance_minutes=tolerance,
            connectivity_windows=windows,
            connectivity_timezone="America/Sao_Paulo",
        ),
        module_ids=modules,
    )
    spy = spy or Spy()
    manager.register("spy", spy)
    return manager, spy


def _logger():
    logger = logging.getLogger(f"test.connectivity.{id(object())}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    events = Events()
    logger.addHandler(events)
    return logger, events


async def _feed(manager, module_id, status, when, logger):
    status = {"E": MonitorStatus.ERROR, "O": MonitorStatus.OK}[status]
    await manager.handle_result(
        module_id=module_id,
        result=MonitorResult(
            status, "m", "ConnectError: [Errno -3] Temporary failure in name resolution"
            if status == MonitorStatus.ERROR else None, 10.0,
            None if status == MonitorStatus.ERROR else [],
        ),
        module_config=ModuleConfig(module_id, "https://example.com/", 60, 10.0, "ua",
                                   RuleConfig("status", "major"), [], True),
        level_name="INFO",
        event_name="monitor_check",
        event_time=when,
        http_client=AsyncMock(),
        logger=logger,
    )


def _at(clock, day=_DAY, shift=timedelta()):
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    return day + timedelta(hours=hours, minutes=minutes, seconds=seconds) + shift


def _outage_events(shift=timedelta(), oks=4, rows=_OUTAGE_2026_11_16, day=_DAY):
    events = []
    for module_id, first, errors, recovered in rows:
        start = _at(first, day, shift)
        events += [(start + timedelta(minutes=i), module_id, "E") for i in range(errors)]
        ok = _at(recovered, day, shift)
        events += [(ok + timedelta(minutes=i), module_id, "O") for i in range(oks)]
    return sorted(events)


async def _replay(manager, events, logger, until=None):
    for when, module_id, status in events:
        if until is not None and when >= until:
            return
        await _feed(manager, module_id, status, when, logger)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

async def test_the_router_reboot_of_2026_11_16_is_one_silent_loss():
    manager, spy = _manager()
    logger, events = _logger()
    await _replay(manager, _outage_events(), logger)

    assert spy.calls == []
    assert events.count("connectivity_lost") == 1
    assert events.count("connectivity_restored") == 1
    assert events.count("monitor_failure") == 0
    assert events.count("monitor_blind_window") == 0
    assert manager._connectivity is None
    assert manager._error_state == {}


async def test_the_loss_starts_at_the_first_error_and_ends_at_the_quorum():
    manager, _ = _manager()
    logger, events = _logger()
    await _replay(manager, _outage_events(), logger)
    restored = next(r for r in events.records if r.event == "connectivity_restored")
    # bitbucket failed first (07:00:15); the 4th recovery (07:06:18) left 6/10 failing.
    assert "2026-11-16T07:00:15+00:00 to 2026-11-16T07:06:18+00:00 (6 min)" in restored.reason


async def test_with_zero_tolerance_the_loss_is_announced_once_with_its_real_times():
    manager, spy = _manager(tolerance=0)
    logger, events = _logger()
    await _replay(manager, _outage_events(), logger)

    # Announced when the loss opened (bitbucket's 3rd error, all 10 failing by then),
    # dated by its real start for the channels that show it.
    assert spy.calls == [
        ("monitor_error", CONNECTIVITY_MODULE_ID, _at("07:02:15")),
        ("monitor_recovered", CONNECTIVITY_MODULE_ID, _at("07:06:18")),
    ]
    assert spy.starts == ["2026-11-16T07:00:15+00:00"]
    assert events.count("monitor_failure") == 0


async def test_a_long_loss_is_announced_while_it_lasts():
    rows = tuple((m, first, errors + 20, _shift_clock(ok, 20)) for m, first, errors, ok
                 in _OUTAGE_2026_11_16)
    manager, spy = _manager(tolerance=10)
    logger, _ = _logger()
    events = _outage_events(rows=rows)
    await _replay(manager, events, logger, until=_at("07:11:00"))
    assert spy.kinds() == ["monitor_error"]
    await _replay(manager, [e for e in events if e[0] >= _at("07:11:00")], logger)
    # Repeated every repeat window while open, like any monitoring failure (Alertmanager
    # expires an alert that is not refreshed), always with the same start.
    kinds = spy.kinds()
    assert kinds[0] == "monitor_error" and kinds[-1] == "monitor_recovered"
    assert kinds.count("monitor_recovered") == 1
    assert kinds.count("monitor_error") == 2
    assert set(spy.starts) == {"2026-11-16T07:00:15+00:00"}


def _shift_clock(clock, minutes):
    moved = _at(clock) + timedelta(minutes=minutes)
    return moved.strftime("%H:%M:%S")


async def test_an_isolated_provider_failure_pages_as_before():
    manager, spy = _manager()
    logger, events = _logger()
    for i in range(3):
        await _feed(manager, "github", "E", _at("07:00:00") + timedelta(minutes=i), logger)
    assert spy.calls == [("monitor_error", "github", _at("07:02:00"))]
    assert events.count("connectivity_lost") == 0


async def test_below_the_quorum_every_module_pages_on_its_own():
    manager, spy = _manager()
    logger, events = _logger()
    failing = _MODULES[:6]  # 60% < 70%
    for i in range(3):
        for module_id in failing:
            await _feed(manager, module_id, "E", _at("07:00:00") + timedelta(minutes=i), logger)
    assert spy.kinds() == ["monitor_error"] * 6
    assert events.count("connectivity_lost") == 0


async def test_with_fewer_than_three_modules_the_detection_is_off():
    manager, spy = _manager(modules=("aws", "gcp"))
    logger, events = _logger()
    for i in range(3):
        for module_id in ("aws", "gcp"):
            await _feed(manager, module_id, "E", _at("07:00:00") + timedelta(minutes=i), logger)
    assert spy.kinds() == ["monitor_error", "monitor_error"]
    assert events.count("connectivity_lost") == 0


async def test_a_bare_manager_keeps_the_previous_behaviour():
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
        )
    )
    spy = Spy()
    manager.register("spy", spy)
    logger, _ = _logger()
    await _replay(manager, _outage_events(), logger)
    assert spy.kinds().count("monitor_error") == 10


async def test_a_provider_still_failing_after_the_loss_counts_from_its_end():
    rows = tuple(
        (m, first, 12 if m == "github" else errors, "07:20:00" if m == "github" else ok)
        for m, first, errors, ok in _OUTAGE_2026_11_16
    )
    manager, spy = _manager()
    logger, events = _logger()
    await _replay(manager, _outage_events(rows=rows), logger)

    # The loss ends at 07:06:18 (the 4th recovery). github keeps failing at 07:07:06,
    # 07:08:06 and 07:09:06: three errors counted from the end of the loss.
    assert spy.calls[0] == ("monitor_error", "github", _at("07:09:06"))
    assert events.count("connectivity_restored") == 1


async def test_an_announced_failure_does_not_repeat_during_the_loss():
    manager, spy = _manager(tolerance=60)
    logger, _ = _logger()
    for i in range(3):
        await _feed(manager, "steam", "E", _at("06:40:00") + timedelta(minutes=i), logger)
    assert spy.kinds() == ["monitor_error"]
    rows = tuple((m, first, errors + 15, _shift_clock(ok, 15))
                 for m, first, errors, ok in _OUTAGE_2026_11_16 if m != "steam")
    events = [e for e in _outage_events(rows=rows) if e[2] == "E"]
    steam_errors = [(_at("06:43:00") + timedelta(minutes=i), "steam", "E") for i in range(35)]
    await _replay(manager, sorted(events + steam_errors), logger)
    # The repeat at 06:52 comes before the loss; while it is open (07:02 onwards) the
    # repeat window passes several times and steam stays quiet.
    steam = [when for kind, module_id, when in spy.calls if module_id == "steam"]
    assert steam == [_at("06:42:00"), _at("06:52:00")]


# ---------------------------------------------------------------------------
# Expected windows (America/Sao_Paulo is UTC-3: 04:00 local is 07:00Z)
# ---------------------------------------------------------------------------

async def test_a_loss_inside_its_window_never_notifies():
    manager, spy = _manager(tolerance=0, windows=((240, 255),))
    logger, events = _logger()
    await _replay(manager, _outage_events(), logger)
    assert spy.calls == []
    assert events.count("connectivity_restored") == 1


async def test_a_loss_that_outlasts_its_window_is_announced_after_the_window():
    manager, spy = _manager(tolerance=0, windows=((240, 255),))
    logger, _ = _logger()
    shift = timedelta(minutes=10)  # 07:10:15Z -> 07:16:18Z, the window ends at 07:15Z
    events = _outage_events(shift=shift)
    await _replay(manager, events, logger, until=_at("07:15:00"))
    assert spy.calls == []
    await _replay(manager, [e for e in events if e[0] >= _at("07:15:00")], logger)
    assert spy.kinds() == ["monitor_error", "monitor_recovered"]
    assert spy.calls[0][2] > _at("07:15:00")
    assert spy.starts == ["2026-11-16T07:10:15+00:00"]


async def test_an_isolated_failure_inside_the_window_still_pages():
    manager, spy = _manager(windows=((240, 255),))
    logger, _ = _logger()
    for i in range(3):
        await _feed(manager, "github", "E", _at("07:05:00") + timedelta(minutes=i), logger)
    assert spy.calls == [("monitor_error", "github", _at("07:07:00"))]


async def test_a_window_across_midnight():
    manager, spy = _manager(tolerance=0, windows=((23 * 60 + 50, 10),))
    logger, events = _logger()
    # 23:55 local on 2026-11-15 is 02:55Z on 2026-11-16.
    await _replay(manager, _outage_events(shift=timedelta(hours=-4, minutes=-5)), logger)
    assert spy.calls == []
    assert events.count("connectivity_restored") == 1


async def test_without_windows_the_tolerance_decides():
    manager, spy = _manager(tolerance=5)
    logger, _ = _logger()
    await _replay(manager, _outage_events(), logger)
    assert spy.kinds() == ["monitor_error", "monitor_recovered"]


# ---------------------------------------------------------------------------
# Delivery and restart
# ---------------------------------------------------------------------------

async def test_a_refused_announcement_is_retried_and_keeps_its_order():
    spy = Spy(up=False)
    manager, _ = _manager(tolerance=0, spy=spy)
    logger, _ = _logger()
    events = _outage_events(oks=6)
    cut = _at("07:07:00")
    await _replay(manager, events, logger, until=cut)
    assert set(spy.kinds()) == {"monitor_error"}
    spy.up = True
    spy.calls.clear()
    await _replay(manager, [e for e in events if e[0] >= cut], logger)
    assert spy.kinds() == ["monitor_error", "monitor_recovered"]
    assert manager._connectivity is None


async def test_a_restart_in_the_middle_of_the_loss_keeps_its_start(tmp_path):
    path = str(tmp_path / "state.json")
    # Anchored so the cut lands ~25 minutes ago: `restore` discards a loss seen in the
    # future or more than a day ago, so a midnight anchor broke this before 07:04 UTC.
    day = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=7, minutes=30)
    events = _outage_events(day=day)
    cut = day + timedelta(hours=7, minutes=4)
    before, _ = _manager(tolerance=0, state_path=path, spy=Spy(up=False))
    logger, _ = _logger()
    await _replay(before, events, logger, until=cut)
    assert before._connectivity is not None

    after, spy = _manager(tolerance=0, state_path=path)
    assert after._connectivity.started_at == day + timedelta(hours=7, seconds=15)
    await _replay(after, [e for e in events if e[0] >= cut], logger)
    assert spy.starts == [(day + timedelta(hours=7, seconds=15)).isoformat()]
    assert spy.kinds() == ["monitor_error", "monitor_recovered"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

async def test_the_configuration_comes_from_the_environment(monkeypatch):
    drain_config_warnings()
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_QUORUM", "0.8")
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES", "15")
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_WINDOWS", "04:00-04:15, 23:50-00:10")
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_TIMEZONE", "UTC")
    config = load_app_config().notifications
    assert config.connectivity_quorum == 0.8
    assert config.connectivity_tolerance_minutes == 15
    assert config.connectivity_windows == ((240, 255), (1430, 10))
    assert config.connectivity_timezone == "UTC"
    assert drain_config_warnings() == []


async def test_defaults_without_the_environment(monkeypatch):
    for name in ("QUORUM", "TOLERANCE_MINUTES", "WINDOWS", "TIMEZONE"):
        monkeypatch.delenv(f"NOTIFICATION_CONNECTIVITY_{name}", raising=False)
    config = load_app_config().notifications
    assert (config.connectivity_quorum, config.connectivity_tolerance_minutes,
            config.connectivity_windows, config.connectivity_timezone) == (
        0.7, 10, (), "America/Sao_Paulo")


async def test_bad_values_warn_and_fall_back(monkeypatch):
    drain_config_warnings()
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_QUORUM", "1.5")
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES", "-1")
    monkeypatch.setenv(
        "NOTIFICATION_CONNECTIVITY_WINDOWS", "04:00-04:15,25:00-01:00,abc,05:00-05:00,4:7-5:00"
    )
    monkeypatch.setenv("NOTIFICATION_CONNECTIVITY_TIMEZONE", "Mars/Olympus")
    config = load_app_config().notifications
    warnings = drain_config_warnings()
    assert config.connectivity_quorum == 0.7
    assert config.connectivity_tolerance_minutes == 10
    assert config.connectivity_windows == ((240, 255),)
    assert config.connectivity_timezone == "America/Sao_Paulo"
    assert len([w for w in warnings if "NOTIFICATION_CONNECTIVITY" in w]) == 7


async def test_an_unreadable_loss_in_the_state_file_is_ignored(tmp_path):
    import json

    from app.core.state_store import SCHEMA_VERSION

    path = tmp_path / "state.json"
    for section in ({"started_at": "nope"}, {"started_at": 5}, ["x"], {"ended_at": None}):
        path.write_text(json.dumps({"version": SCHEMA_VERSION, "alerts": {}, "errors": {},
                                    "connectivity": section}), encoding="utf-8")
        manager, _ = _manager(state_path=str(path))
        assert manager._connectivity is None


async def test_a_state_file_without_the_new_fields_still_loads(tmp_path):
    import json

    from app.core.state_store import SCHEMA_VERSION, StateStore

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": SCHEMA_VERSION, "alerts": {}, "errors": {
        "steam": {"consecutive_errors": 2, "last_notified_at": None, "last_reason": "x",
                  "consecutive_successes": 0}}}), encoding="utf-8")
    _, errors = StateStore(str(path)).load()
    assert errors["steam"].first_error_at is None
    assert errors["steam"].connectivity_loss is False


# ---------------------------------------------------------------------------
# Bug-hunter attacks on the first version (#106)
# ---------------------------------------------------------------------------

async def test_a_provider_failing_alone_does_not_backdate_the_loss():
    manager, spy = _manager(tolerance=10, windows=((240, 255),))
    logger, events = _logger()
    for minute in range(300):  # aws alone from 02:00Z to 06:59Z, announced on its own
        await _feed(manager, "aws", "E", _at("02:00:00") + timedelta(minutes=minute), logger)
    for i in range(3):
        for module_id in _MODULES:
            if module_id != "aws":
                await _feed(manager, module_id, "E",
                            _at("07:01:00") + timedelta(minutes=i), logger)
    for module_id in _MODULES:
        if module_id != "aws":
            await _feed(manager, module_id, "O", _at("07:05:00"), logger)
    assert [c for c in spy.calls if c[1] == CONNECTIVITY_MODULE_ID] == []
    lost = next(r for r in events.records if r.event == "connectivity_lost")
    assert "since 2026-11-16T07:01:00" in lost.reason


class SlowSpy(Spy):
    async def _record(self, kind, kwargs):
        import asyncio

        await asyncio.sleep(0.01)
        return await super()._record(kind, kwargs)


async def _all_fail(manager, logger, start, times=3):
    for i in range(times):
        for module_id in _MODULES:
            await _feed(manager, module_id, "E", start + timedelta(minutes=i), logger)


async def test_concurrent_results_announce_the_loss_once():
    import asyncio

    manager, spy = _manager(tolerance=10, spy=SlowSpy())
    logger, _ = _logger()
    await _all_fail(manager, logger, _at("07:00:00"))
    await asyncio.gather(*(
        _feed(manager, module_id, "E", _at("07:11:00"), logger) for module_id in _MODULES
    ))
    assert spy.kinds(CONNECTIVITY_MODULE_ID) == ["monitor_error"]


async def test_concurrent_recoveries_announce_the_end_once():
    import asyncio

    manager, spy = _manager(tolerance=0, spy=SlowSpy())
    logger, _ = _logger()
    await _all_fail(manager, logger, _at("07:00:00"))
    await asyncio.gather(*(
        _feed(manager, module_id, "O", _at("07:05:00"), logger) for module_id in _MODULES
    ))
    assert spy.kinds(CONNECTIVITY_MODULE_ID) == ["monitor_error", "monitor_recovered"]


async def test_an_announced_loss_is_refreshed_every_repeat_window():
    manager, spy = _manager(tolerance=10)
    logger, _ = _logger()
    await _all_fail(manager, logger, _at("07:00:00"), times=40)
    announced = [when for kind, module_id, when in spy.calls
                 if module_id == CONNECTIVITY_MODULE_ID]
    # Due at 07:10, then every 10 minutes while open: Alertmanager keeps it firing.
    assert announced == [_at("07:10:00"), _at("07:20:00"), _at("07:30:00")]


async def test_a_loss_restored_after_a_long_downtime_is_not_paged(tmp_path):
    path = str(tmp_path / "state.json")
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
    before, _ = _manager(state_path=path, spy=Spy(up=False))
    logger, _ = _logger()
    await _all_fail(before, logger, start)
    assert before._connectivity is not None

    after, spy = _manager(state_path=path)
    assert after._connectivity is None
    await _feed(after, "aws", "O", datetime.now(timezone.utc), logger)
    assert spy.calls == []


async def test_a_refused_attempt_before_the_quorum_still_logs_the_blind_window():
    manager, spy = _manager(spy=Spy(up=False))
    logger, events = _logger()
    for i in range(3):  # aws alone reaches the threshold: attempted, refused
        await _feed(manager, "aws", "E", _at("07:00:00") + timedelta(minutes=i), logger)
    await _all_fail(manager, logger, _at("07:03:00"))
    for module_id in _MODULES:
        await _feed(manager, module_id, "O", _at("07:06:00"), logger)
    blind = [r for r in events.records if getattr(r, "event", None) == "monitor_blind_window"]
    assert [r.module_id for r in blind] == ["aws"]


async def test_the_reported_start_reaches_the_summary_without_breaking_its_cooldown():
    from app.notifications.telegram.notifier import TelegramNotifier

    from tests.test_telegram_daily_summary import _Bot

    notifier = TelegramNotifier(TelegramConfig(
        True, "token", ["1"], "https://api.telegram.org", "%H:%M", "UTC",
        mode="daily_summary", summary_timezone="America/Sao_Paulo",
    ))
    bot = _Bot()
    logger, _ = _logger()
    error = MonitorResult(MonitorStatus.ERROR, "monitoring failure", "x", 1.0,
                          [{"started_at": _at("07:00:15").isoformat()}])
    common = dict(module_id=CONNECTIVITY_MODULE_ID, interval_seconds=60, level_name="ERROR",
                  event_name="monitor_failure", http_client=bot, logger=logger)
    assert await notifier.send_monitor_error(result=error, event_time=_at("07:10:15"), **common)
    entry = notifier._summary.entries[0]
    assert entry.started_at == _at("07:00:15")
    assert notifier._summary.last_resend_at == _at("07:10:15")
    assert "04:00" in bot.last("sendMessage")["text"]
