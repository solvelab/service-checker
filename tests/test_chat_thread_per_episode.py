"""One Google Chat thread per alert episode (#150).

The key used to be the component alone, so a component failing again days later replied
under the previous episode's "Recovered". It now carries the alert start, which the
manager puts on the alert, every repeat and the recovery, and persists across restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.types import MonitorResult, MonitorStatus
from app.notifications.google_chat.notifier import GoogleChatNotifier, _thread_key
from tests.test_google_chat_card import _T0, _chat_config, _feed, _item, _manager

_START = datetime(2026, 11, 19, 16, 1, tzinfo=timezone.utc)


def _keys(results):
    return [_thread_key("openai", r) for r in results]


@pytest.mark.asyncio
async def test_alert_repeats_and_recovery_share_one_thread():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_item()], 0)
    await _feed(manager, MonitorStatus.ALERT, [_item()], 11)
    await _feed(manager, MonitorStatus.OK, [], 30)
    keys = set(_keys(spy.alerts + spy.recoveries))
    assert keys == {f"openai:resp:{int(_T0.timestamp())}"}


@pytest.mark.asyncio
async def test_a_new_episode_of_the_same_component_opens_a_new_thread():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_item()], 0)
    await _feed(manager, MonitorStatus.OK, [], 30)
    await _feed(manager, MonitorStatus.ALERT, [_item()], 60 * 24)
    first, second = _keys(spy.alerts)
    assert first != second
    assert _keys(spy.recoveries) == [first]


@pytest.mark.asyncio
async def test_a_restart_mid_episode_keeps_the_thread(tmp_path):
    path = tmp_path / "state.json"
    before, spy_before = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_item()], 0)
    after, spy_after = _manager(path)
    await _feed(after, MonitorStatus.ALERT, [_item()], 11)
    await _feed(after, MonitorStatus.OK, [], 30)
    assert set(_keys(spy_after.alerts + spy_after.recoveries)) == set(_keys(spy_before.alerts))


def test_without_a_start_the_key_is_the_component_as_before():
    result = MonitorResult(MonitorStatus.ERROR, "m", "r", 1.0, [{"id": "resp"}])
    assert _thread_key("openai", result) == "openai:resp"
    assert _thread_key("openai", MonitorResult(MonitorStatus.ERROR, "m", "r", 1.0, None)) == "openai"


def test_the_card_carries_the_episode_key():
    result = MonitorResult(MonitorStatus.ALERT, "m", "r", 1.0, [_item()], started_at=_START)
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200, text="{}"))
    asyncio.run(GoogleChatNotifier(_chat_config()).send_alert(
        module_id="openai", result=result, interval_seconds=60, level_name="WARNING",
        event_name="monitor_check", event_time=_START, http_client=client,
        logger=MagicMock(spec=logging.Logger)))
    body = client.post.call_args.kwargs["json"]
    assert body["thread"]["threadKey"] == f"openai:resp:{int(_START.timestamp())}"
    assert "messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" in client.post.call_args.args[0]


# -- coverage the bug-hunter probed ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_vanished_recovery_shares_the_episode_thread():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_item()], 0)
    await _feed(manager, MonitorStatus.ALERT, [_item(id="other", name="Codex")], 1)
    assert spy.recoveries and _keys(spy.recoveries)[0] == _keys(spy.alerts)[0]


@pytest.mark.asyncio
async def test_a_module_level_episode_shares_one_thread():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, {"hero": "x"}, 0)
    await _feed(manager, MonitorStatus.ALERT, {"hero": "x"}, 11)
    await _feed(manager, MonitorStatus.OK, None, 30)
    assert set(_keys(spy.alerts + spy.recoveries)) == {f"openai:{int(_T0.timestamp())}"}


@pytest.mark.asyncio
async def test_a_legacy_state_freezes_its_start_at_the_first_repeat(tmp_path):
    import json
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_item()], 0)
    data = json.loads(path.read_text())
    for entry in data["alerts"].values():
        entry.pop("opened_at", None)
    path.write_text(json.dumps(data))
    after, spy = _manager(path)
    await _feed(after, MonitorStatus.ALERT, [_item()], 11)
    await _feed(after, MonitorStatus.ALERT, [_item()], 22)
    await _feed(after, MonitorStatus.OK, [], 30)
    assert len(set(_keys(spy.alerts + spy.recoveries))) == 1
