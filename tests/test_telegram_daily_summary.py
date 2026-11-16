"""The Telegram daily summary (issue #100): one message per chat per day.

Measured on 2026-11-16: an open `claude` incident sent a new card every ten minutes
(~144 a day for one unchanged problem), and a flapping `steam` sent 101 messages in 14h.
The group learned to ignore the chat. These tests pin the replacement: one message per
day, edited in silence, and moved to the bottom with a notification only when a problem
is new.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import NotificationManager
from app.core.state_store import StateStore
from app.core.types import MonitorResult, MonitorStatus
from app.notifications.telegram.notifier import TelegramNotifier
from app.notifications.telegram.summary import MESSAGE_LIMIT

# 14:00 UTC is 11:00 in America/Sao_Paulo.
_T0 = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)
_SEQUENCE = Path(__file__).parent / "fixtures" / "steam" / "check_statuses_2026-11-15.txt"


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else "<html>bad gateway</html>"

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _Bot:
    """Fake Bot API. `script[method]` queues `(status, body)` answers for that method."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.script: dict[str, list] = {}
        self.next_id = 100
        self.live: dict[str, set] = {}  # chat -> message ids that exist

    async def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, dict(json or {})))
        queue = self.script.get(method)
        if queue:
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return _Response(*answer)
        chat = str(json["chat_id"])
        if method == "sendMessage":
            self.next_id += 1
            self.live.setdefault(chat, set()).add(self.next_id)
            return _Response(200, {"ok": True, "result": {"message_id": self.next_id}})
        if method == "deleteMessage":
            self.live.get(chat, set()).discard(json["message_id"])
        return _Response(200, {"ok": True, "result": True})

    def methods(self):
        return [method for method, _ in self.calls]

    def last(self, method):
        return [body for name, body in self.calls if name == method][-1]

    def notifications(self):
        return [b for m, b in self.calls if m == "sendMessage" and not b["disable_notification"]]


def _config(chats=("1",), **kwargs):
    return TelegramConfig(True, "token", list(chats), "https://api.telegram.org",
                          "%Y", "UTC", mode="daily_summary",
                          summary_timezone="America/Sao_Paulo", **kwargs)


def _notifier(chats=("1",), max_age_minutes=1440):
    return TelegramNotifier(_config(chats), max_age_minutes=max_age_minutes)


def _alert(name="Steam Community", status="minor", item_id=None):
    item = {"id": item_id or name.lower().replace(" ", "-"), "name": name, "status": status}
    return MonitorResult(MonitorStatus.ALERT, "service degraded", f"{name}: {status}",
                         10.0, [item])


def _recovery(name="Steam Community", item_id=None):
    item = {"id": item_id or name.lower().replace(" ", "-"), "name": name,
            "from_status": "minor", "to_status": "operational"}
    return MonitorResult(MonitorStatus.OK, "service restored", name, 10.0, [item])


def _monitor_error(last="UpstreamChallenged: upstream challenged the automated client"):
    return MonitorResult(MonitorStatus.ERROR, "monitoring failure",
                         f"3 consecutive failed checks; last error: {last}", 10.0)


async def _send(notifier, bot, method, result, minutes, module="steam"):
    return await getattr(notifier, method)(
        module_id=module, result=result, interval_seconds=60, level_name="INFO",
        event_name="e", event_time=_T0 + timedelta(minutes=minutes),
        http_client=bot, logger=MagicMock(spec=logging.Logger),
    )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_first_problem_sends_the_day_message_with_a_notification():
    bot, notifier = _Bot(), _notifier()
    assert await _send(notifier, bot, "send_alert", _alert(), 0) is True
    assert bot.methods() == ["sendMessage"]
    body = bot.last("sendMessage")
    assert body["disable_notification"] is False
    assert body["parse_mode"] == "HTML"
    assert "16/11/2026" in body["text"]
    assert "STEAM" in body["text"] and "Steam Community" in body["text"]
    assert "desde 11:00" in body["text"]              # America/Sao_Paulo, not UTC


@pytest.mark.asyncio
async def test_a_repeat_with_nothing_new_costs_no_api_call():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(), 0)
    assert await _send(notifier, bot, "send_alert", _alert(), 10) is True
    assert bot.methods() == ["sendMessage"]


@pytest.mark.asyncio
async def test_a_repeat_with_a_new_status_edits_in_silence():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(status="minor"), 0)
    await _send(notifier, bot, "send_alert", _alert(status="major"), 10)
    assert bot.methods() == ["sendMessage", "editMessageText"]
    assert "major" in bot.last("editMessageText")["text"]
    assert bot.last("editMessageText")["message_id"] == 101


@pytest.mark.asyncio
async def test_a_recovery_edits_with_start_end_and_duration():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(), 0)
    assert await _send(notifier, bot, "send_recovery", _recovery(), 33) is True
    assert bot.methods() == ["sendMessage", "editMessageText"]
    text = bot.last("editMessageText")["text"]
    assert "✅ Steam Community · 11:00 → 11:33 (33 min)" in text
    assert "Tudo operacional" in text


@pytest.mark.asyncio
async def test_a_new_problem_after_the_window_moves_the_message_down():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), 0)
    await _send(notifier, bot, "send_alert", _alert("Steam Store"), 5)
    assert bot.methods() == ["sendMessage", "deleteMessage", "sendMessage"]
    assert bot.calls[1][1]["message_id"] == 101
    assert bot.last("sendMessage")["disable_notification"] is False
    assert bot.live["1"] == {102}                     # still one message in the chat


@pytest.mark.asyncio
async def test_several_new_problems_in_one_burst_notify_once():
    bot, notifier = _Bot(), _notifier()
    for name in ("Steam Community", "Steam Store", "Steam Web API"):
        await _send(notifier, bot, "send_alert", _alert(name), 0)
    assert bot.methods() == ["sendMessage", "editMessageText", "editMessageText"]
    assert len(bot.notifications()) == 1
    assert "3 problema(s) em aberto" in bot.last("editMessageText")["text"]


@pytest.mark.asyncio
async def test_the_same_problem_after_its_recovery_is_new_again():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(), 0)
    await _send(notifier, bot, "send_recovery", _recovery(), 20)
    await _send(notifier, bot, "send_alert", _alert(), 40)
    assert len(bot.notifications()) == 2
    assert "2 problema(s) hoje" in bot.last("sendMessage")["text"]


@pytest.mark.asyncio
async def test_monitoring_failure_has_its_own_section_with_the_reason():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_monitor_error", _monitor_error(), 0)
    text = bot.last("sendMessage")["text"]
    assert bot.last("sendMessage")["disable_notification"] is False
    assert "Falhas de monitoramento" in text
    assert "steam · desde 11:00 — UpstreamChallenged: upstream challenged" in text
    assert "consecutive failed checks" not in text

    restored = MonitorResult(MonitorStatus.OK, "monitoring restored",
                             "upstream reachable again for 3 checks in a row", 10.0)
    await _send(notifier, bot, "send_monitor_recovered", restored, 12)
    assert "✅ steam · 11:00 → 11:12 (12 min)" in bot.last("editMessageText")["text"]


@pytest.mark.asyncio
async def test_a_module_level_alert_uses_the_module_as_the_problem():
    bot, notifier = _Bot(), _notifier()
    alert = MonitorResult(MonitorStatus.ALERT, "degraded", "claude.ai: degraded_performance",
                          10.0, None)
    await _send(notifier, bot, "send_alert", alert, 0, module="claude")
    ok = MonitorResult(MonitorStatus.OK, "ok", None, 10.0, None)
    await _send(notifier, bot, "send_recovery", ok, 90, module="claude")
    text = bot.last("editMessageText")["text"]
    assert "✅ claude · 11:00 → 12:30 (1h30)" in text


@pytest.mark.asyncio
async def test_a_recovery_of_a_problem_opened_before_the_summary_is_still_shown():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_recovery", _recovery(), 0)
    text = bot.last("sendMessage")["text"]
    assert bot.last("sendMessage")["disable_notification"] is True
    assert "✅ Steam Community · resolvido às 11:00" in text


@pytest.mark.asyncio
async def test_names_are_html_escaped():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(name="API <v2> & co"), 0)
    assert "API &lt;v2&gt; &amp; co" in bot.last("sendMessage")["text"]


# ---------------------------------------------------------------------------
# Day rollover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_day_rolls_at_midnight_in_the_summary_zone_and_carries_open_problems():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), 0)    # 11:00
    await _send(notifier, bot, "send_alert", _alert("Steam Store"), 5)
    await _send(notifier, bot, "send_recovery", _recovery("Steam Store"), 30)
    # 02:59 UTC on the 17th is still the 16th in Sao Paulo.
    late = (datetime(2026, 11, 17, 2, 59, tzinfo=timezone.utc) - _T0).total_seconds() / 60
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), late)
    calls_before = len(bot.calls)
    # 03:01 UTC on the 17th is 00:01 on the 17th in Sao Paulo: a repeat of the open problem.
    after = (datetime(2026, 11, 17, 3, 1, tzinfo=timezone.utc) - _T0).total_seconds() / 60
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), after)

    new_calls = bot.calls[calls_before:]
    assert [m for m, _ in new_calls] == ["sendMessage"]        # the old one is kept
    body = new_calls[0][1]
    assert body["disable_notification"] is True                 # a repeat does not ring
    assert "17/11/2026" in body["text"]
    assert "Steam Community — minor · desde 16/11 11:00" in body["text"]
    assert "Steam Store" not in body["text"]                    # resolved stays with the 16th


@pytest.mark.asyncio
async def test_an_open_problem_nobody_confirmed_is_not_carried():
    bot, notifier = _Bot(), _notifier(max_age_minutes=60)
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), 0)
    next_day = 24 * 60
    await _send(notifier, bot, "send_alert", _alert("Steam Store"), next_day)
    text = bot.last("sendMessage")["text"]
    assert "Steam Store" in text and "Steam Community" not in text


# ---------------------------------------------------------------------------
# Bot API failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_not_modified_counts_as_delivered():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(status="minor"), 0)
    bot.script["editMessageText"] = [(400, {"ok": False, "description":
                                            "Bad Request: message is not modified: specified "
                                            "new message content and reply markup are exactly "
                                            "the same"})]
    assert await _send(notifier, bot, "send_alert", _alert(status="major"), 10) is True
    assert bot.methods() == ["sendMessage", "editMessageText"]


@pytest.mark.asyncio
async def test_a_message_that_cannot_be_edited_is_replaced_in_silence():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(status="minor"), 0)
    bot.script["editMessageText"] = [(400, {"ok": False, "description":
                                            "Bad Request: message to edit not found"})]
    assert await _send(notifier, bot, "send_alert", _alert(status="major"), 10) is True
    assert bot.methods() == ["sendMessage", "editMessageText", "sendMessage"]
    assert bot.last("sendMessage")["disable_notification"] is True
    await _send(notifier, bot, "send_alert", _alert(status="critical"), 20)
    assert bot.last("editMessageText")["message_id"] == 102     # the replacement


@pytest.mark.parametrize("answer", [(429, {"ok": False, "description": "Too Many Requests"}),
                                    (502, None), ConnectionError("reset")])
@pytest.mark.asyncio
async def test_a_transient_edit_failure_is_not_delivered_and_sends_nothing_else(answer):
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(status="minor"), 0)
    bot.script["editMessageText"] = [answer]
    assert await _send(notifier, bot, "send_alert", _alert(status="major"), 10) is False
    assert bot.methods() == ["sendMessage", "editMessageText"]
    # The next event retries the edit.
    assert await _send(notifier, bot, "send_alert", _alert(status="major"), 20) is True
    assert bot.methods()[-1] == "editMessageText"


@pytest.mark.asyncio
async def test_a_failed_delete_does_not_stop_the_resend():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert("Steam Community"), 0)
    bot.script["deleteMessage"] = [(400, {"ok": False,
                                          "description": "Bad Request: message can't be deleted"})]
    assert await _send(notifier, bot, "send_alert", _alert("Steam Store"), 5) is True
    assert bot.methods() == ["sendMessage", "deleteMessage", "sendMessage"]
    assert bot.last("sendMessage")["disable_notification"] is False


@pytest.mark.asyncio
async def test_when_every_chat_fails_the_retry_still_notifies():
    bot, notifier = _Bot(), _notifier(chats=("1", "2"))
    bot.script["sendMessage"] = [(500, None), (403, {"ok": False, "description": "Forbidden"})]
    assert await _send(notifier, bot, "send_alert", _alert(), 0) is False
    assert await _send(notifier, bot, "send_alert", _alert(), 1) is True
    retry = bot.calls[2:]
    assert [m for m, _ in retry] == ["sendMessage", "sendMessage"]   # one per chat
    assert all(not b["disable_notification"] for _, b in retry)


@pytest.mark.asyncio
async def test_one_chat_accepting_is_delivered():
    bot, notifier = _Bot(), _notifier(chats=("1", "2"))
    bot.script["sendMessage"] = [(500, None)]
    assert await _send(notifier, bot, "send_alert", _alert(), 0) is True
    assert notifier.export_state()["message_ids"] == {"2": 101}


@pytest.mark.asyncio
async def test_a_send_without_a_message_id_falls_back_to_a_new_message_next_time():
    bot, notifier = _Bot(), _notifier()
    bot.script["sendMessage"] = [(200, {"ok": True, "result": {}})]
    assert await _send(notifier, bot, "send_alert", _alert(status="minor"), 0) is True
    await _send(notifier, bot, "send_alert", _alert(status="major"), 10)
    assert bot.methods() == ["sendMessage", "sendMessage"]


@pytest.mark.asyncio
async def test_missing_token_is_not_delivered():
    bot = _Bot()
    notifier = TelegramNotifier(TelegramConfig(True, None, ["1"], "https://x", "%Y", "UTC",
                                               mode="daily_summary"))
    assert await _send(notifier, bot, "send_alert", _alert(), 0) is False
    assert bot.calls == []


# ---------------------------------------------------------------------------
# Rendering limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_oversized_day_is_compacted_and_keeps_every_open_problem():
    bot, notifier = _Bot(), _notifier()
    for i in range(120):
        name = f"Component number {i:03d} with a long descriptive name"
        await _send(notifier, bot, "send_alert", _alert(name), i * 2)
        await _send(notifier, bot, "send_recovery", _recovery(name), i * 2 + 1)
    await _send(notifier, bot, "send_alert", _alert("Still Broken"), 300)
    text = bot.last("sendMessage")["text"]
    assert len(text) <= MESSAGE_LIMIT
    assert "Still Broken" in text
    assert "e mais" in text


# ---------------------------------------------------------------------------
# Restart and persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_restart_with_state_keeps_editing_the_same_message():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert(status="minor"), 0)
    state = json.loads(json.dumps(notifier.export_state()))      # survives JSON

    restarted = _notifier()
    restarted.restore_state(state)
    await _send(restarted, bot, "send_alert", _alert(status="major"), 10)
    assert bot.methods() == ["sendMessage", "editMessageText"]
    assert bot.last("editMessageText")["message_id"] == 101
    assert len(bot.notifications()) == 1                          # not new after a restart


def test_a_corrupt_saved_summary_is_dropped_part_by_part():
    notifier = _notifier()
    notifier.restore_state({
        "day": _T0.date().isoformat(),
        "message_ids": {"1": 101, "2": "x"},
        "entries": [
            {"key": "steam:a", "module_id": "steam", "kind": "service",
             "last_seen_at": _T0.isoformat(), "started_at": _T0.isoformat()},
            {"key": "steam:b", "module_id": "steam", "kind": "weird",
             "last_seen_at": _T0.isoformat()},
            "garbage",
        ],
    })
    state = notifier.export_state()
    assert [e["key"] for e in state["entries"]] == ["steam:a"]
    assert state["message_ids"] == {"1": 101}


def test_a_summary_from_the_future_is_ignored():
    notifier = _notifier()
    notifier.restore_state({"day": "2999-01-01", "message_ids": {"1": 5}, "entries": []})
    assert notifier.export_state()["day"] is None
    assert notifier.export_state()["message_ids"] == {}


def test_the_store_keeps_the_section_beside_alerts_without_a_new_version(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({}, {}, {"telegram_summary": {"day": "2026-11-16"}, "unknown": {"x": 1}})
    document = json.loads(path.read_text())
    assert document["version"] == 1
    assert document["telegram_summary"] == {"day": "2026-11-16"}
    assert "unknown" not in document

    reloaded = StateStore(str(path))
    reloaded.load()
    assert reloaded.sections == {"telegram_summary": {"day": "2026-11-16"}}


def test_an_older_state_file_loads_without_a_summary(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "alerts": {}, "errors": {}}))
    store = StateStore(str(path))
    store.load()
    assert store.sections == {}


# ---------------------------------------------------------------------------
# Through the real manager
# ---------------------------------------------------------------------------

def _module_config(slug="steam"):
    return ModuleConfig(slug, "https://example/", 60, 10.0, "ua",
                        RuleConfig("status", "major"), [], True)


def _manager(state_path=None, mode="daily_summary"):
    telegram = TelegramConfig(True, "token", ["1"], "https://api.telegram.org", "%Y", "UTC",
                              mode=mode, summary_timezone="America/Sao_Paulo")
    return NotificationManager(NotificationConfig(
        telegram=telegram,
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10, error_threshold=3, recovery_threshold=3,
        state_path=state_path,
    ))


async def _handle(manager, bot, status, minute, payload=None, module="steam", reason=None):
    await manager.handle_result(
        module_id=module,
        result=MonitorResult(status, "m", reason, 10.0, payload),
        module_config=_module_config(module),
        level_name="INFO", event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=bot, logger=MagicMock(spec=logging.Logger),
    )


_ITEM = [{"id": "community", "name": "Steam Community", "status": "minor"}]


@pytest.mark.asyncio
async def test_a_two_hour_alert_is_one_message_and_one_notification():
    """Per event this was 12 alert cards plus a recovery card."""
    bot, manager = _Bot(), _manager()
    for minute in range(120):
        await _handle(manager, bot, MonitorStatus.ALERT, minute, _ITEM)
    await _handle(manager, bot, MonitorStatus.OK, 120, [])
    assert bot.methods() == ["sendMessage", "editMessageText"]
    assert len(bot.notifications()) == 1
    assert "11:00 → 13:00 (2h00)" in bot.last("editMessageText")["text"]


@pytest.mark.asyncio
async def test_a_restart_through_the_manager_keeps_the_message(tmp_path, monkeypatch):
    # The store and the summary judge "stale" and "future" against the wall clock.
    clock = lambda: _T0 + timedelta(minutes=30)  # noqa: E731
    monkeypatch.setattr("app.core.state_store._now", clock)
    monkeypatch.setattr("app.notifications.telegram.notifier._utcnow", clock)
    path = str(tmp_path / "state.json")
    bot = _Bot()
    await _handle(_manager(path), bot, MonitorStatus.ALERT, 0, _ITEM)
    restarted = _manager(path)
    await _handle(restarted, bot, MonitorStatus.OK, 30, [])
    assert bot.methods() == ["sendMessage", "editMessageText"]
    assert bot.last("editMessageText")["message_id"] == 101


@pytest.mark.asyncio
async def test_per_event_mode_through_the_manager_is_unchanged():
    bot, manager = _Bot(), _manager(mode="per_event")
    for minute in range(25):
        await _handle(manager, bot, MonitorStatus.ALERT, minute, _ITEM)
    assert bot.methods() == ["sendMessage"] * 3                  # minutes 0, 10, 20
    assert "editMessageText" not in bot.methods()


def _production_sequence():
    lines = _SEQUENCE.read_text(encoding="utf-8").splitlines()
    return "".join(line for line in lines if not line.startswith("#"))


@pytest.mark.asyncio
async def test_replaying_the_steam_day_keeps_one_message_per_day_and_rings_per_new_problem():
    """The #87 day, replayed through the real manager and the summary."""
    bot, manager = _Bot(), _manager()
    for minute, status in enumerate(_production_sequence()):
        if status == "E":
            await _handle(manager, bot, MonitorStatus.ERROR, minute,
                          reason="UpstreamRejected: upstream returned HTTP 403")
        elif status == "A":
            await _handle(manager, bot, MonitorStatus.ALERT, minute, _ITEM)
        else:
            await _handle(manager, bot, MonitorStatus.OK, minute, [])

    # 814 checks from 11:00 crossing midnight in Sao Paulo: per event this was 101
    # messages. Now each day keeps exactly one live message — every resend deleted the
    # one before it — and the chat rings only when a problem opens.
    assert bot.live == {"1": {117, 119}}                          # the 16th and the 17th
    assert bot.methods().count("sendMessage") == 19
    assert bot.methods().count("deleteMessage") == 17
    assert len(bot.notifications()) == 18
    assert len(bot.calls) == 52


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    for name in ("TELEGRAM_MODE", "TELEGRAM_SUMMARY_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_env_default_is_the_daily_summary_in_sao_paulo(env):
    from app.core.config import _load_notification_config

    telegram = _load_notification_config().telegram
    assert telegram.mode == "daily_summary"
    assert telegram.summary_timezone == "America/Sao_Paulo"


def test_the_dataclass_default_is_per_event():
    """Hand-built configs (the contract tests) keep describing the old mode."""
    config = TelegramConfig(True, "t", ["1"], "https://x", "%Y", "UTC")
    assert config.mode == "per_event"
    assert TelegramNotifier(config).export_state() is None


@pytest.mark.parametrize("name, value", [("TELEGRAM_MODE", "digest"),
                                         ("TELEGRAM_SUMMARY_TIMEZONE", "Mars/Olympus")])
def test_an_invalid_setting_falls_back_loudly(env, name, value):
    from app.core.config import _load_notification_config, drain_config_warnings

    drain_config_warnings()
    env.setenv(name, value)
    telegram = _load_notification_config().telegram
    assert telegram.mode == "daily_summary"
    assert telegram.summary_timezone == "America/Sao_Paulo"
    warnings = drain_config_warnings()
    assert len(warnings) == 1 and name in warnings[0]


def test_per_event_is_accepted(env):
    from app.core.config import _load_notification_config

    env.setenv("TELEGRAM_MODE", "PER_EVENT")
    assert _load_notification_config().telegram.mode == "per_event"


# ---------------------------------------------------------------------------
# Bug-hunter findings on the first draft (issue #100)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_recovery_of_a_component_without_id_closes_its_entry():
    """The digest key hashed the recovery's from/to_status too, so it never matched."""
    bot, notifier = _Bot(), _notifier()
    item = {"status": "minor", "description": "no identifier at all"}
    alert = MonitorResult(MonitorStatus.ALERT, "degraded", "x", 10.0, [dict(item)])
    recovery = MonitorResult(MonitorStatus.OK, "restored", "x", 10.0,
                             [{**item, "from_status": "minor", "to_status": "operational"}])
    await _send(notifier, bot, "send_alert", alert, 0)
    await _send(notifier, bot, "send_recovery", recovery, 10)
    entries = notifier.export_state()["entries"]
    assert len(entries) == 1 and entries[0]["resolved_at"] is not None


@pytest.mark.asyncio
async def test_a_digest_keyed_component_closes_through_the_manager_vanished_path():
    bot, manager = _Bot(), _manager()
    item = [{"status": "minor", "description": "no identifier at all"}]
    await _handle(manager, bot, MonitorStatus.ALERT, 0, item)
    await _handle(manager, bot, MonitorStatus.OK, 5, [])
    entries = manager.telegram_notifier.export_state()["entries"]
    assert len(entries) == 1 and entries[0]["resolved_at"] is not None


@pytest.mark.parametrize("section", [
    {"day": "2026-11-16", "message_ids": ["x"], "entries": 5},
    {"day": 7, "message_ids": "x", "entries": "y", "last_resend_at": 3},
    {"day": "2026-11-16", "message_ids": {"1": True}, "entries": [None, 1, []]},
])
def test_a_malformed_saved_summary_never_raises_at_startup(tmp_path, section):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "alerts": {}, "errors": {},
                                "telegram_summary": section}))
    manager = _manager(str(path))                                   # must not raise
    state = manager.telegram_notifier.export_state()
    assert state["entries"] == [] and state["message_ids"] == {}


@pytest.mark.asyncio
async def test_a_failed_new_problem_still_rings_when_another_event_writes_first():
    """A silent edit of another problem used to mark the failed one as notified."""
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_alert", _alert("A"), 0)
    bot.script["deleteMessage"] = [(502, None)]
    bot.script["sendMessage"] = [(502, None)]
    assert await _send(notifier, bot, "send_alert", _alert("Y"), 5) is False
    rings = len(bot.notifications())
    await _send(notifier, bot, "send_alert", _alert("A", status="major"), 6)
    assert len(bot.notifications()) == rings + 1                    # Y's ring is paid here
    assert "Y" in bot.last("sendMessage")["text"]
    await _send(notifier, bot, "send_alert", _alert("Y"), 7)        # the manager's retry
    assert len(bot.notifications()) == rings + 1                    # and not paid twice


@pytest.mark.asyncio
async def test_an_event_stamped_before_midnight_does_not_roll_the_day_back():
    bot, notifier = _Bot(), _notifier()
    midnight = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)    # 00:00 in Sao Paulo
    offset = lambda dt: (dt - _T0).total_seconds() / 60            # noqa: E731
    await _send(notifier, bot, "send_alert", _alert("A"), offset(midnight + timedelta(minutes=1)))
    day, ids = notifier.export_state()["day"], dict(notifier.export_state()["message_ids"])
    calls = len(bot.calls)
    await _send(notifier, bot, "send_alert", _alert("A", status="major"),
                offset(midnight - timedelta(seconds=1)))
    assert notifier.export_state()["day"] == day
    assert notifier.export_state()["message_ids"] == ids
    assert [m for m, _ in bot.calls[calls:]] == ["editMessageText"]


@pytest.mark.asyncio
async def test_a_refused_recovery_edit_is_retried_by_the_next_cycle():
    """The manager drops a service recovery whatever the channel answers."""
    bot, manager = _Bot(), _manager()
    await _handle(manager, bot, MonitorStatus.ALERT, 0, _ITEM)
    too_many = (429, {"ok": False, "description": "Too Many Requests"})
    bot.script["editMessageText"] = [too_many, too_many]
    await _handle(manager, bot, MonitorStatus.OK, 5, [])     # refused, and its flush too
    await _handle(manager, bot, MonitorStatus.OK, 6, [], module="claude")   # any cycle
    edits = [b for m, b in bot.calls if m == "editMessageText"]
    assert len(edits) == 3       # refused, refused again by the flush, then accepted
    assert "Tudo operacional" in edits[-1]["text"]
    calls = len(bot.calls)
    await _handle(manager, bot, MonitorStatus.OK, 7, [])
    assert len(bot.calls) == calls                                  # nothing left to fix


@pytest.mark.asyncio
async def test_a_quiet_flush_makes_no_api_call():
    bot, manager = _Bot(), _manager()
    for minute in range(5):
        await _handle(manager, bot, MonitorStatus.OK, minute, [])
    assert bot.calls == []


@pytest.mark.asyncio
async def test_a_chat_whose_resend_failed_after_its_delete_rings_on_the_next_write():
    bot, notifier = _Bot(), _notifier(chats=("1", "2"))
    await _send(notifier, bot, "send_alert", _alert("A"), 0)
    bot.script["sendMessage"] = [(200, {"ok": True, "result": {"message_id": 555}}),
                                 (502, None)]
    assert await _send(notifier, bot, "send_alert", _alert("Y"), 5) is True
    assert "2" not in notifier.export_state()["message_ids"]        # deleted id dropped
    await _send(notifier, bot, "send_alert", _alert("Y", status="major"), 6)
    chat2 = [b for m, b in bot.calls[-2:] if b["chat_id"] == "2"]
    assert chat2 and chat2[-1]["disable_notification"] is False     # the owed ring
    assert [m for m, b in bot.calls[-2:] if b["chat_id"] == "1"] == ["editMessageText"]


@pytest.mark.parametrize("field", ["resolved_at", "started_at"])
def test_a_corrupt_time_drops_the_entry_instead_of_reopening_it(field):
    notifier = _notifier()
    entry = {"key": "steam:a", "module_id": "steam", "kind": "service",
             "last_seen_at": _T0.isoformat(), "started_at": _T0.isoformat(),
             "resolved_at": _T0.isoformat(), field: "garbage"}
    notifier.restore_state({"day": _T0.date().isoformat(), "entries": [entry]})
    assert notifier.export_state()["entries"] == []


@pytest.mark.asyncio
async def test_unlimited_state_age_still_stops_carrying_an_unconfirmed_problem():
    bot, notifier = _Bot(), _notifier(max_age_minutes=0)
    await _send(notifier, bot, "send_alert", _alert("Ghost"), 0)
    await _send(notifier, bot, "send_alert", _alert("Fresh"), 3 * 24 * 60)
    text = bot.last("sendMessage")["text"]
    assert "Fresh" in text and "Ghost" not in text


@pytest.mark.asyncio
async def test_an_oversized_day_fits_in_utf16_units_and_counts_what_it_cut():
    from app.notifications.telegram.summary import telegram_length

    bot, notifier = _Bot(), _notifier()
    for i in range(200):
        await _send(notifier, bot, "send_alert", _alert(f"Open component {i:03d}"), i * 2)
    text = bot.last("sendMessage")["text"] if bot.methods()[-1] == "sendMessage" \
        else bot.last("editMessageText")["text"]
    assert telegram_length(text) <= MESSAGE_LIMIT
    assert "linha(s) omitidas" in text
    before = text
    await _send(notifier, bot, "send_alert", _alert("One more"), 401)
    after = [b for m, b in bot.calls if m in ("sendMessage", "editMessageText")][-1]["text"]
    assert after != before                                          # a hidden new one shows


@pytest.mark.asyncio
async def test_a_module_named_monitoring_cannot_close_another_modules_failure():
    bot, notifier = _Bot(), _notifier()
    await _send(notifier, bot, "send_monitor_error", _monitor_error(), 0)
    await _send(notifier, bot, "send_recovery", _recovery("steam", item_id="steam"), 5,
                module="monitoring")
    monitoring = [e for e in notifier.export_state()["entries"] if e["kind"] == "monitoring"]
    assert monitoring[0]["resolved_at"] is None
