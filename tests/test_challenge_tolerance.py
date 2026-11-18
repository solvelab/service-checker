"""Challenge tolerance (issue #112).

steamstat.us sits behind Cloudflare Bot Management, which challenges the checker at a rate
that moves on its own. Between 05:53Z and 11:53Z on 2026-11-17, 219 of 344 steam checks
ended in `UpstreamChallenged`; streaks lasted up to 45 minutes and opened six monitoring
failures (21 `monitor_failure` with the repeats) while Steam itself had no incident. The
replay below is every steam check of that window, from the production log.
"""
from __future__ import annotations

import json
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
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus

pytestmark = pytest.mark.asyncio

#: Every steam check of 2026-11-17 05:53Z-11:53Z: "HH:MM:SS" UTC plus C (challenged
#: ERROR), E (any other ERROR: the router reboot's DNS failures) or O (OK/ALERT).
_STEAM_2026_11_17 = """
    05:53:47C 05:54:48O 05:55:51C 05:56:55O 05:57:57O 05:59:01O 06:00:03O 06:01:04O
    06:02:07C 06:03:10O 06:04:11O 06:05:11O 06:06:14O 06:07:18O 06:08:19O 06:09:20O
    06:10:20O 06:11:23C 06:12:26C 06:13:30C 06:14:34C 06:15:37C 06:16:41C 06:17:44C
    06:18:47C 06:19:50C 06:20:51O 06:21:54C 06:22:57C 06:24:00C 06:25:04C 06:26:07C
    06:27:10C 06:28:13C 06:29:17C 06:30:20C 06:31:23C 06:32:27C 06:33:30C 06:34:30O
    06:35:30O 06:36:34O 06:37:38C 06:38:41C 06:39:44C 06:40:49C 06:41:52C 06:42:55C
    06:43:58C 06:45:02C 06:46:05C 06:47:08C 06:48:11C 06:49:15C 06:50:18C 06:51:21C
    06:52:25C 06:53:28C 06:54:31C 06:55:33O 06:56:36C 06:57:39C 06:58:43C 06:59:43O
    07:00:58E 07:02:01E 07:03:04E 07:04:07E 07:05:13O 07:06:13O 07:07:14O 07:08:18C
    07:09:18O 07:10:18O 07:11:19O 07:12:19O 07:13:19O 07:14:19O 07:15:20O 07:16:23C
    07:17:26C 07:18:28O 07:19:31C 07:20:33O 07:21:36O 07:22:40C 07:23:43C 07:24:46C
    07:25:51C 07:26:54C 07:27:57C 07:29:01C 07:30:04C 07:31:10C 07:32:15C 07:33:19C
    07:34:24C 07:35:27C 07:36:30C 07:37:33C 07:38:37C 07:39:40O 07:40:45C 07:41:49C
    07:42:52C 07:43:55C 07:44:58C 07:46:02C 07:47:05C 07:48:08C 07:49:12C 07:50:15C
    07:51:18C 07:52:21C 07:53:24C 07:54:28C 07:55:31C 07:56:34C 07:57:38C 07:58:41C
    07:59:44C 08:00:47C 08:01:50C 08:02:54C 08:03:57C 08:05:00C 08:06:03C 08:07:07C
    08:08:10C 08:09:13C 08:10:16C 08:11:18O 08:12:22C 08:13:25C 08:14:28C 08:15:30O
    08:16:33C 08:17:33O 08:18:37O 08:19:40C 08:20:42O 08:21:45C 08:22:49C 08:23:52C
    08:24:55C 08:25:58C 08:27:01C 08:28:05C 08:29:08C 08:30:11C 08:31:14C 08:32:18C
    08:33:21C 08:34:24C 08:35:26O 08:36:29C 08:37:33C 08:38:36C 08:39:39C 08:40:42O
    08:41:46C 08:42:49C 08:43:52C 08:44:55C 08:45:59C 08:47:02C 08:48:05C 08:49:08C
    08:50:12C 08:51:15C 08:52:18C 08:53:22C 08:54:25C 08:55:28C 08:56:31C 08:57:35C
    08:58:38C 08:59:41C 09:00:44C 09:01:48C 09:02:51C 09:03:54C 09:04:57C 09:06:01C
    09:07:04C 09:08:07C 09:09:10C 09:10:14C 09:11:17C 09:12:20C 09:13:23C 09:14:27C
    09:15:30C 09:16:33C 09:17:36C 09:18:40C 09:19:43C 09:20:46C 09:21:49C 09:22:53C
    09:23:56C 09:24:59C 09:26:03C 09:27:04O 09:28:05O 09:29:08C 09:30:11C 09:31:12O
    09:32:12O 09:33:15O 09:34:20C 09:35:20O 09:36:23O 09:37:26C 09:38:30O 09:39:30O
    09:40:30O 09:41:31O 09:42:34C 09:43:34O 09:44:37C 09:45:38O 09:46:38O 09:47:41O
    09:48:42O 09:49:45C 09:50:47O 09:51:47O 09:52:47O 09:53:47O 09:54:51O 09:55:54O
    09:56:54O 09:57:58O 09:58:58O 10:00:02O 10:01:05C 10:02:08C 10:03:11C 10:04:13O
    10:05:16C 10:06:19C 10:07:20O 10:08:20O 10:09:23C 10:10:27C 10:11:30C 10:12:33C
    10:13:36C 10:14:40C 10:15:43C 10:16:46C 10:17:50C 10:18:53C 10:19:56C 10:20:59C
    10:22:03C 10:23:06C 10:24:09C 10:25:12C 10:26:16O 10:27:19C 10:28:22C 10:29:26C
    10:30:29C 10:31:29O 10:32:32C 10:33:35C 10:34:39C 10:35:42C 10:36:42O 10:37:45C
    10:38:49C 10:39:52C 10:40:52O 10:41:56O 10:42:58O 10:44:02C 10:45:04O 10:46:07C
    10:47:10C 10:48:13C 10:49:18C 10:50:20O 10:51:23C 10:52:26C 10:53:30C 10:54:30O
    10:55:30O 10:56:33C 10:57:34O 10:58:36O 10:59:37O 11:00:38O 11:01:39O 11:02:39O
    11:03:41O 11:04:43O 11:05:43O 11:06:46C 11:07:50O 11:08:50O 11:09:53O 11:10:55O
    11:11:57O 11:12:58O 11:13:58O 11:14:58O 11:15:59O 11:16:59O 11:17:59O 11:18:59O
    11:20:00O 11:21:00O 11:22:00O 11:23:01O 11:24:01O 11:25:01O 11:26:01O 11:27:05O
    11:28:07O 11:29:10C 11:30:10O 11:31:14O 11:32:17O 11:33:17O 11:34:18O 11:35:21C
    11:36:25O 11:37:28C 11:38:28O 11:39:28O 11:40:29O 11:41:32C 11:42:32O 11:43:36C
    11:44:39C 11:45:39O 11:46:41O 11:47:44C 11:48:46O 11:49:49C 11:50:50O 11:51:50O
    11:52:53O
"""

_OTHERS = ("aws", "bitbucket", "claude", "cloudflare", "gcp",
           "github", "oci", "openai", "rockstar")
_DAY = datetime(2026, 11, 17, tzinfo=timezone.utc)
_T0 = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
_CHALLENGE = ("UpstreamChallenged: upstream challenged the automated client "
              "(Cloudflare cf-mitigated: challenge, HTTP 403)")


class Spy:
    def __init__(self, up=True):
        self.up = up
        self.errors: list[MonitorResult] = []
        self.calls: list[tuple[str, str]] = []

    async def _record(self, kind, kwargs):
        self.calls.append((kind, kwargs["module_id"]))
        if kind == "monitor_error":
            self.errors.append(kwargs["result"])
        return self.up

    async def send_alert(self, **kwargs):
        return await self._record("alert", kwargs)

    async def send_recovery(self, **kwargs):
        return await self._record("recovery", kwargs)

    async def send_monitor_error(self, **kwargs):
        return await self._record("monitor_error", kwargs)

    async def send_monitor_recovered(self, **kwargs):
        return await self._record("monitor_recovered", kwargs)


class Events(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def of(self, event):
        return [r for r in self.records if getattr(r, "event", None) == event]


def _manager(tolerance=60, *, modules=(), state_path=None, spy=None):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
            recovery_threshold=3,
            state_path=state_path,
            challenge_tolerance_minutes=tolerance,
        ),
        module_ids=modules,
    )
    spy = spy or Spy()
    manager.register("spy", spy)
    return manager, spy


def _logger():
    logger = logging.getLogger(f"test.challenge.{id(object())}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    events = Events()
    logger.addHandler(events)
    return logger, events


async def _feed(manager, kind, when, logger, module_id="steam"):
    if kind == "O":
        result = MonitorResult(MonitorStatus.OK, "m", None, 10.0, [])
    elif kind == "C":
        result = MonitorResult(MonitorStatus.ERROR, "m", _CHALLENGE, 10.0, challenged=True)
    else:
        result = MonitorResult(MonitorStatus.ERROR, "m",
                               "DNSError: Failed to perform, curl: (6)", 10.0)
    await manager.handle_result(
        module_id=module_id,
        result=result,
        module_config=ModuleConfig(module_id, "https://example.com/", 60, 10.0, "ua",
                                   RuleConfig("status", "major"), [], True),
        level_name="INFO",
        event_name="monitor_check",
        event_time=when,
        http_client=AsyncMock(),
        logger=logger,
    )


async def _run(manager, pattern, logger, start=_T0, module_id="steam"):
    """One check a minute: `pattern` is a string of C, E and O."""
    for minute, kind in enumerate(pattern):
        await _feed(manager, kind, start + timedelta(minutes=minute), logger, module_id)


def _checks():
    for token in _STEAM_2026_11_17.split():
        hours, minutes, seconds = (int(part) for part in token[:8].split(":"))
        yield _DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds), token[8]


async def _replay(manager, logger):
    # The router reboot (the E checks) took all ten modules down together, as in
    # production, so the connectivity loss detection (#106) is what absorbs it.
    for when, kind in _checks():
        if kind == "E" or kind == "O":
            for other in _OTHERS:
                await _feed(manager, "E" if kind == "E" else "O", when, logger, other)
        await _feed(manager, kind, when, logger)


# ---------------------------------------------------------------------------
# The morning of 2026-11-17
# ---------------------------------------------------------------------------

async def test_the_replay_has_the_measured_shape():
    kinds = [kind for _, kind in _checks()]
    # 219 ERROR, 125 OK and one ALERT; the issue's "219 of 344" left the ALERT out.
    assert len(kinds) == 345
    assert kinds.count("C") + kinds.count("E") == 219
    assert kinds.count("E") == 4


async def test_the_challenged_morning_pages_nothing_with_the_default_tolerance():
    manager, spy = _manager(modules=("steam",) + _OTHERS)
    logger, events = _logger()
    await _replay(manager, logger)

    assert spy.calls == []
    assert events.of("monitor_blind_window") == []
    assert len(events.of("monitor_challenge_absorbed")) == 17
    assert len(events.of("connectivity_lost")) == 1


async def test_without_the_tolerance_the_same_morning_still_pages():
    # The baseline: what production sent before this change.
    manager, spy = _manager(tolerance=0, modules=("steam",) + _OTHERS)
    logger, _ = _logger()
    await _replay(manager, logger)

    # Production sent 21 with 6 recoveries; the replay starts with no prior state, so the
    # streak already open at 05:53Z is one page short.
    assert len(spy.errors) == 20
    assert [kind for kind, _ in spy.calls].count("monitor_recovered") == 5


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

async def test_a_challenge_streak_shorter_than_the_tolerance_only_logs():
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, "C" * 45 + "O", logger)

    assert spy.calls == []
    [absorbed] = events.of("monitor_challenge_absorbed")
    assert absorbed.module_id == "steam"
    assert absorbed.reason.startswith(f"45 consecutive challenged check(s) from {_T0.isoformat()}")
    assert events.of("monitor_blind_window") == []


async def test_a_challenge_streak_that_reaches_the_tolerance_pages_with_its_real_start():
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, "C" * 60, logger)
    assert spy.errors == []

    await _run(manager, "C", logger, start=_T0 + timedelta(minutes=60))
    [error] = spy.errors
    assert "last error: UpstreamChallenged" in error.reason
    assert error.payload == [{"started_at": _T0.isoformat()}]

    await _run(manager, "OOO", logger, start=_T0 + timedelta(minutes=61))
    assert [kind for kind, _ in spy.calls] == ["monitor_error", "monitor_recovered"]
    assert events.of("monitor_challenge_absorbed") == []


async def test_an_error_that_is_not_a_challenge_pages_at_the_plain_threshold():
    # The challenges before it are not inherited: the threshold counts from the first
    # error that is not one (#114).
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, "CCEE", logger)
    assert spy.errors == []

    await _run(manager, "E", logger, start=_T0 + timedelta(minutes=4))
    [error] = spy.errors
    assert "3 consecutive failed checks; last error: DNSError" in error.reason
    assert error.payload is None
    # Two challenges never reached the threshold, so nothing was held.
    assert events.of("monitor_challenge_absorbed") == []


async def test_a_held_streak_that_turns_into_another_error_starts_a_new_streak():
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, "CCCCC", logger)
    await _run(manager, "EE", logger, start=_T0 + timedelta(minutes=5))
    assert spy.errors == []
    [absorbed] = events.of("monitor_challenge_absorbed")
    assert absorbed.reason.startswith(
        f"5 consecutive challenged check(s) from {_T0.isoformat()} "
        f"to {(_T0 + timedelta(minutes=5)).isoformat()}")

    await _run(manager, "E", logger, start=_T0 + timedelta(minutes=7))
    [error] = spy.errors
    assert "3 consecutive failed checks" in error.reason
    # Not a late challenge page: the failure is the new streak, dated by the channel.
    assert error.payload is None

    await _run(manager, "OOO", logger, start=_T0 + timedelta(minutes=8))
    assert [kind for kind, _ in spy.calls] == ["monitor_error", "monitor_recovered"]
    assert events.of("monitor_blind_window") == []
    assert len(events.of("monitor_challenge_absorbed")) == 1


async def test_with_the_tolerance_off_another_error_does_not_restart_the_streak():
    manager, spy = _manager(tolerance=0)
    logger, events = _logger()
    await _run(manager, "CCE", logger)

    [error] = spy.errors
    assert "3 consecutive failed checks; last error: DNSError" in error.reason
    assert events.of("monitor_challenge_absorbed") == []


async def test_an_announced_challenge_failure_does_not_restart_on_another_error():
    manager, spy = _manager()
    logger, _ = _logger()
    await _run(manager, "C" * 61, logger)
    assert len(spy.errors) == 1

    await _run(manager, "E" * 10, logger, start=_T0 + timedelta(minutes=61))
    # Still the same failure: one repeat at the 10-minute window, the count goes on.
    assert len(spy.errors) == 2
    assert spy.errors[1].reason.startswith("71 consecutive failed checks")
    assert spy.errors[1].payload is None


async def test_a_refused_late_page_that_turns_into_another_error_is_still_a_blind_window():
    # Past the tolerance the send was attempted: the error must not wipe it (bug-hunter).
    manager, spy = _manager(spy=Spy(up=False))
    logger, events = _logger()
    await _run(manager, "C" * 61 + "EO", logger)

    [blind] = events.of("monitor_blind_window")
    assert blind.reason.startswith("62 consecutive failed check(s)")
    assert events.of("monitor_challenge_absorbed") == []


async def test_a_refused_late_page_is_retried_on_the_next_error_without_restarting():
    manager, spy = _manager(spy=Spy(up=False))
    logger, _ = _logger()
    await _run(manager, "C" * 61, logger)
    spy.up = True

    await _run(manager, "E", logger, start=_T0 + timedelta(minutes=61))
    assert len(spy.errors) == 2
    assert spy.errors[1].reason.startswith("62 consecutive failed checks")


async def test_a_stray_error_between_held_streaks_pages_nothing():
    # A choice, not an accident: like `EEO`, a lone error under the threshold is
    # transient, and each held part is logged (#114).
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, ("C" * 59 + "EO") * 3, logger)

    assert spy.calls == []
    assert len(events.of("monitor_challenge_absorbed")) == 3


async def test_a_restart_keeps_the_new_streak_start(tmp_path):
    path = str(tmp_path / "state.json")
    manager, _ = _manager(state_path=path)
    logger, _ = _logger()
    await _run(manager, "CCCCEE", logger)

    manager, spy = _manager(state_path=path)
    logger, events = _logger()
    await _run(manager, "E", logger, start=_T0 + timedelta(minutes=6))
    [error] = spy.errors
    assert "3 consecutive failed checks" in error.reason


async def test_zero_turns_the_tolerance_off():
    manager, spy = _manager(tolerance=0)
    logger, _ = _logger()
    await _run(manager, "CCC", logger)

    [error] = spy.errors
    assert "UpstreamChallenged" in error.reason


async def test_rockstar_follows_the_same_rule():
    manager, spy = _manager()
    logger, events = _logger()
    await _run(manager, "C" * 20 + "O", logger, module_id="rockstar")

    assert spy.calls == []
    assert events.of("monitor_challenge_absorbed")[0].module_id == "rockstar"


async def test_an_announced_failure_keeps_repeating_through_challenges():
    # The tolerance holds only a failure nobody heard of yet.
    manager, spy = _manager()
    logger, _ = _logger()
    await _run(manager, "EEE" + "C" * 11, logger)

    assert len(spy.errors) == 2


async def test_a_refused_page_after_the_tolerance_is_a_blind_window_again():
    manager, spy = _manager(spy=Spy(up=False))
    logger, events = _logger()
    await _run(manager, "C" * 62 + "O", logger)

    assert len(spy.errors) == 2
    assert len(events.of("monitor_blind_window")) == 1
    assert events.of("monitor_challenge_absorbed") == []


# ---------------------------------------------------------------------------
# Restart and configuration
# ---------------------------------------------------------------------------

async def test_a_restart_keeps_the_streak_start(tmp_path):
    path = str(tmp_path / "state.json")
    manager, _ = _manager(state_path=path)
    logger, _ = _logger()
    await _run(manager, "C" * 30, logger)

    manager, spy = _manager(state_path=path)
    await _run(manager, "C" * 30, logger, start=_T0 + timedelta(minutes=30))
    assert spy.errors == []
    await _run(manager, "C", logger, start=_T0 + timedelta(minutes=60))
    [error] = spy.errors
    assert error.payload == [{"started_at": _T0.isoformat()}]


async def test_a_restart_keeps_a_held_streak_out_of_the_blind_window(tmp_path):
    path = str(tmp_path / "state.json")
    manager, _ = _manager(state_path=path)
    logger, _ = _logger()
    await _run(manager, "CCCC", logger)

    manager, spy = _manager(state_path=path)
    logger, events = _logger()
    await _run(manager, "O", logger, start=_T0 + timedelta(minutes=4))
    assert spy.calls == []
    assert events.of("monitor_blind_window") == []
    assert len(events.of("monitor_challenge_absorbed")) == 1


async def test_the_tolerance_comes_from_the_environment(monkeypatch):
    drain_config_warnings()
    monkeypatch.setenv("NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES", "90")
    assert load_app_config().notifications.challenge_tolerance_minutes == 90


async def test_the_tolerance_defaults_to_an_hour(monkeypatch):
    drain_config_warnings()
    monkeypatch.delenv("NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES", raising=False)
    assert load_app_config().notifications.challenge_tolerance_minutes == 60
    assert drain_config_warnings() == []


@pytest.mark.parametrize("raw", ["-5", "6O"])
async def test_an_invalid_tolerance_warns_and_falls_back(monkeypatch, raw):
    drain_config_warnings()
    monkeypatch.setenv("NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES", raw)
    assert load_app_config().notifications.challenge_tolerance_minutes == 60
    assert any("NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES" in w for w in drain_config_warnings())


# ---------------------------------------------------------------------------
# Found by the bug-hunter
# ---------------------------------------------------------------------------

_SIX = ("steam", "rockstar", "aws", "gcp", "github", "oci")


async def test_a_connectivity_blip_does_not_restart_the_challenge_clock():
    manager, spy = _manager(modules=("steam",) + _OTHERS)
    logger, _ = _logger()
    for minute in range(61):
        when = _T0 + timedelta(minutes=minute)
        if 50 <= minute <= 53:
            for other in _OTHERS:
                await _feed(manager, "E" if minute < 53 else "O", when, logger, other)
        await _feed(manager, "C", when, logger)

    [error] = spy.errors
    assert error.payload == [{"started_at": _T0.isoformat()}]


@pytest.mark.parametrize("tolerance", [0, 60])
async def test_a_streak_start_in_the_future_does_not_hold_the_page(tmp_path, tolerance):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": 1, "alerts": {},
        "errors": {"steam": {"consecutive_errors": 5, "last_notified_at": None,
                             "last_reason": _CHALLENGE, "consecutive_successes": 0,
                             "first_error_at": (_T0 + timedelta(days=1)).isoformat(),
                             "challenge_only": True}},
    }))
    manager, spy = _manager(tolerance, state_path=str(path))
    logger, _ = _logger()
    await _run(manager, "C", logger)

    assert len(spy.errors) == 1


async def test_a_retried_late_page_keeps_its_real_start():
    manager, spy = _manager(spy=Spy(up=False))
    logger, _ = _logger()
    await _run(manager, "C" * 64, logger)

    assert len(spy.errors) == 4
    assert all(e.payload == [{"started_at": _T0.isoformat()}] for e in spy.errors)


async def test_challenged_modules_do_not_count_toward_the_connectivity_quorum():
    # 4 of 6 failing for real is below 0.7; the two challenged must not lift it to 6/6.
    manager, spy = _manager(modules=_SIX)
    logger, events = _logger()
    for minute in range(8):
        when = _T0 + timedelta(minutes=minute)
        for module_id in _SIX:
            kind = "C" if module_id in ("steam", "rockstar") else "E"
            await _feed(manager, kind, when, logger, module_id)

    assert events.of("connectivity_lost") == []
    assert {m for kind, m in spy.calls if kind == "monitor_error"} == {
        "aws", "gcp", "github", "oci"}


async def test_a_held_streak_that_meets_a_connectivity_loss_is_logged_once_as_absorbed():
    # The challenge part ends at the first network error (#114); the loss then absorbs
    # the new streak, and the recovery logs nothing more.
    modules = ("steam", "aws", "gcp", "github")
    manager, spy = _manager(modules=modules)
    logger, events = _logger()
    await _run(manager, "CCCC", logger)
    for minute in range(4, 7):
        for module_id in modules:
            await _feed(manager, "E", _T0 + timedelta(minutes=minute), logger, module_id)
    await _feed(manager, "O", _T0 + timedelta(minutes=7), logger)

    assert spy.calls == []
    [absorbed] = events.of("monitor_challenge_absorbed")
    assert absorbed.reason.startswith("4 consecutive challenged check(s)")
    assert events.of("monitor_blind_window") == []


# ---------------------------------------------------------------------------
# The router outage of 2026-11-18 (#114)
# ---------------------------------------------------------------------------

_MODULES = ("steam", "aws", "bitbucket", "claude", "cloudflare",
            "gcp", "github", "oci", "openai", "rockstar")

#: Every check of every module, 2026-11-18 11:38:55Z-12:02:02Z, from the production log:
#: "HH:MM:SS" UTC, then C/E/O as above, then the module's index in `_MODULES`. v2.11.0
#: paged steam at 11:43:20Z: its first DNS error inherited four held challenges, and the
#: connectivity loss opened only at 11:44:55Z.
_OUTAGE_2026_11_18 = """
    11:38:55C0 11:39:11O7 11:39:13O4 11:39:14O9 11:39:15O1 11:39:19O6 11:39:23O5 11:39:24O3
    11:39:25O8 11:39:27O2 11:39:58C0 11:40:12O7 11:40:14O4 11:40:15O9 11:40:16O1 11:40:19O6
    11:40:24O5 11:40:24O3 11:40:25O8 11:40:28O2 11:41:01C0 11:41:12O7 11:41:14O4 11:41:15O9
    11:41:17O1 11:41:20O6 11:41:24O5 11:41:24O3 11:41:25O8 11:41:28O2 11:42:05C0 11:42:12O7
    11:42:15O4 11:42:15O9 11:42:17O1 11:42:20O6 11:42:24O5 11:42:24O3 11:42:26O8 11:42:28O2
    11:43:16E7 11:43:19E4 11:43:20E0 11:43:21E1 11:43:24E6 11:43:28E5 11:43:28E3 11:43:30E8
    11:43:30E9 11:43:32E2 11:44:16E7 11:44:19E4 11:44:36E1 11:44:44E6 11:44:55E0 11:45:00E5
    11:45:08E3 11:45:10E8 11:45:10E2 11:45:13E9 11:45:16E7 11:45:19E4 11:45:36E1 11:45:44E6
    11:46:06E5 11:46:12E3 11:46:14E8 11:46:14E0 11:46:14E2 11:46:22E9 11:46:22E7 11:46:23E4
    11:46:40E1 11:46:44E6 11:47:14O5 11:47:24O3 11:47:29O9 11:47:29O2 11:47:29O7 11:47:29O8
    11:47:30O4 11:47:32C0 11:47:40O1 11:47:44O6 11:48:14O5 11:48:24O3 11:48:29O2 11:48:29O7
    11:48:29O9 11:48:30O8 11:48:30O4 11:48:35C0 11:48:41O1 11:48:44O6 11:49:15O5 11:49:24O3
    11:49:30O7 11:49:30O2 11:49:30O9 11:49:30O8 11:49:31O4 11:49:39C0 11:49:42O1 11:49:44O6
    11:50:15O5 11:50:25O3 11:50:30O2 11:50:30O7 11:50:30O9 11:50:30O8 11:50:31O4 11:50:42C0
    11:50:42O1 11:50:44O6 11:51:15O5 11:51:25O3 11:51:30O7 11:51:30O2 11:51:30O9 11:51:31O8
    11:51:32O4 11:51:43O1 11:51:45O6 11:51:46C0 11:52:16O5 11:52:25O3 11:52:30O7 11:52:30O2
    11:52:31O9 11:52:32O8 11:52:33O4 11:52:44O1 11:52:45O6 11:52:50C0 11:53:17O5 11:53:26O3
    11:53:31O7 11:53:31O2 11:53:31O9 11:53:32O8 11:53:33O4 11:53:45O1 11:53:46O6 11:53:50O0
    11:54:17O5 11:54:26O3 11:54:31O7 11:54:31O2 11:54:31O9 11:54:32O8 11:54:34O4 11:54:46O1
    11:54:46O6 11:54:54C0 11:55:18O5 11:55:26O3 11:55:31O7 11:55:31O2 11:55:32O9 11:55:32O8
    11:55:34O4 11:55:46O6 11:55:46O1 11:55:57C0 11:56:18O5 11:56:26O3 11:56:31O2 11:56:31O7
    11:56:32O9 11:56:33O8 11:56:35O4 11:56:46O6 11:56:47O1 11:56:57O0 11:57:18O5 11:57:26O3
    11:57:31O7 11:57:31O2 11:57:32O9 11:57:33O8 11:57:35O4 11:57:46O6 11:57:47O1 11:57:58O0
    11:58:19O5 11:58:27O3 11:58:31O7 11:58:32O2 11:58:33O9 11:58:34O8 11:58:36O4 11:58:46O6
    11:58:48O1 11:59:01C0 11:59:19O5 11:59:27O3 11:59:32O7 11:59:32O2 11:59:33O9 11:59:34O8
    11:59:36O4 11:59:46O6 11:59:49O1 12:00:01O0 12:00:21O5 12:00:27O3 12:00:32O7 12:00:32O2
    12:00:33O9 12:00:34O8 12:00:37O4 12:00:47O6 12:00:49O1 12:01:01O0 12:01:21O5 12:01:27O3
    12:01:32O7 12:01:32O2 12:01:34O9 12:01:34O8 12:01:37O4 12:01:47O6 12:01:50O1 12:02:02O0
"""


async def test_the_router_outage_inside_a_challenge_streak_pages_nothing():
    manager, spy = _manager(modules=_MODULES)
    logger, events = _logger()
    day = datetime(2026, 11, 18, tzinfo=timezone.utc)
    checks = _OUTAGE_2026_11_18.split()
    assert len(checks) == 224
    for token in checks:
        hours, minutes, seconds = (int(part) for part in token[:8].split(":"))
        when = day + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        await _feed(manager, token[8], when, logger, _MODULES[int(token[9])])

    assert spy.calls == []
    assert len(events.of("connectivity_lost")) == 1
    assert len(events.of("connectivity_restored")) == 1
    assert events.of("monitor_blind_window") == []
    absorbed = [r.reason for r in events.of("monitor_challenge_absorbed")]
    assert absorbed[0].startswith("4 consecutive challenged check(s) from 2026-11-18T11:38:55")
