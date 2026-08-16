"""Tests for the notifier registry.

Channels used to be hardcoded: two named attributes and eight dispatch sites, one
per channel per event type. Two consequences this suite pins down — a channel could
ship without one of the four methods and only fail at the first event of that type
in production, and an exception escaping one channel aborted `handle_result`, so a
broken Telegram bot could stop the webhook from ever seeing the alert.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import NotificationManager
from app.core.types import NOTIFIER_METHODS, MonitorResult, MonitorStatus, Notifier

_T0 = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _config(*, telegram=False, webhook=False, threshold=3, repeat_minutes=10):
    return NotificationConfig(
        telegram=TelegramConfig(
            enabled=telegram,
            bot_token="tok",
            chat_ids=["1"],
            api_url="https://api.telegram.org",
            timestamp_format="%Y-%m-%d",
            timestamp_zone="UTC",
        ),
        webhook=WebhookConfig(
            enabled=webhook,
            url="https://hook.example.com",
            token="secret",
            header_name="Authorization",
        ),
        repeat_minutes=repeat_minutes,
        error_threshold=threshold,
    )


def _module_config(slug="rockstar"):
    return ModuleConfig(
        slug=slug,
        url="https://example.com",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value="major"),
        service_filter=[],
        enabled=True,
    )


class RecordingNotifier:
    """A complete channel that records every event it receives."""

    def __init__(self, name="spy"):
        self.name = name
        self.events: list[str] = []

    async def send_alert(self, **kwargs):
        self.events.append("send_alert")

    async def send_recovery(self, **kwargs):
        self.events.append("send_recovery")

    async def send_monitor_error(self, **kwargs):
        self.events.append("send_monitor_error")

    async def send_monitor_recovered(self, **kwargs):
        self.events.append("send_monitor_recovered")


class ExplodingNotifier(RecordingNotifier):
    """A channel that fails on every event, the way a bad token would."""

    async def send_alert(self, **kwargs):
        raise RuntimeError("channel is broken")

    async def send_recovery(self, **kwargs):
        raise RuntimeError("channel is broken")

    async def send_monitor_error(self, **kwargs):
        raise RuntimeError("channel is broken")

    async def send_monitor_recovered(self, **kwargs):
        raise RuntimeError("channel is broken")


class IncompleteNotifier:
    """Missing `send_monitor_recovered` — the method that fires most rarely."""

    async def send_alert(self, **kwargs): ...
    async def send_recovery(self, **kwargs): ...
    async def send_monitor_error(self, **kwargs): ...


async def _feed(manager, result, when, module_id="rockstar", logger=None):
    await manager.handle_result(
        module_id=module_id,
        result=result,
        module_config=_module_config(module_id),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=when),
        http_client=AsyncMock(),
        logger=logger or MagicMock(spec=logging.Logger),
    )


def _alert():
    return MonitorResult(MonitorStatus.ALERT, "degraded", "FiveM: down", 10.0, {"h": 1})


def _ok():
    return MonitorResult(MonitorStatus.OK, "healthy", None, 10.0, {"h": 1})


def _error():
    return MonitorResult(MonitorStatus.ERROR, "failed", "OSError: net", 10.0, None)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_no_channels_enabled_means_no_notifiers():
    manager = NotificationManager(_config())
    assert manager.has_notifiers() is False


def test_enabled_channels_are_registered_from_config():
    manager = NotificationManager(_config(telegram=True, webhook=True))
    assert manager.has_notifiers() is True
    assert manager.telegram_notifier is not None
    assert manager.webhook_notifier is not None


def test_register_accepts_a_complete_channel():
    manager = NotificationManager(_config())
    manager.register("spy", RecordingNotifier())
    assert manager.has_notifiers() is True


def test_register_rejects_a_channel_missing_a_method():
    """The gap must surface at registration, not at the first event of that type."""
    manager = NotificationManager(_config())
    with pytest.raises(TypeError, match="send_monitor_recovered"):
        manager.register("incomplete", IncompleteNotifier())
    assert manager.has_notifiers() is False


def test_register_error_names_the_channel_and_every_missing_method():
    manager = NotificationManager(_config())
    with pytest.raises(TypeError) as excinfo:
        manager.register("bare", object())
    message = str(excinfo.value)
    assert "bare" in message
    for method in NOTIFIER_METHODS:
        assert method in message


def test_unregister_removes_the_channel():
    manager = NotificationManager(_config())
    manager.register("spy", RecordingNotifier())
    manager.unregister("spy")
    assert manager.has_notifiers() is False


def test_unregister_of_an_unknown_channel_is_a_no_op():
    manager = NotificationManager(_config())
    manager.unregister("never-registered")
    assert manager.has_notifiers() is False


def test_registering_the_same_name_twice_replaces_it():
    manager = NotificationManager(_config())
    first, second = RecordingNotifier("a"), RecordingNotifier("b")
    manager.register("spy", first)
    manager.register("spy", second)
    assert manager._notifiers["spy"] is second


def test_the_real_notifiers_satisfy_the_protocol():
    from app.notifications.telegram.notifier import TelegramNotifier
    from app.notifications.webhook.notifier import WebhookNotifier

    config = _config(telegram=True, webhook=True)
    for notifier in (TelegramNotifier(config.telegram), WebhookNotifier(config.webhook)):
        assert isinstance(notifier, Notifier)
        for method in NOTIFIER_METHODS:
            assert callable(getattr(notifier, method))


# ---------------------------------------------------------------------------
# Dispatch reaches every channel, for every event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_registered_channel_receives_all_four_events():
    manager = NotificationManager(_config(threshold=1))
    spy = RecordingNotifier()
    manager.register("spy", spy)

    await _feed(manager, _alert(), 0)          # send_alert
    await _feed(manager, _error(), 1)          # send_monitor_error (threshold 1)
    await _feed(manager, _ok(), 2)             # send_monitor_recovered + send_recovery

    assert set(spy.events) == set(NOTIFIER_METHODS)


@pytest.mark.asyncio
async def test_every_registered_channel_receives_the_same_event():
    manager = NotificationManager(_config())
    spies = [RecordingNotifier(f"spy{i}") for i in range(3)]
    for i, spy in enumerate(spies):
        manager.register(f"spy{i}", spy)

    await _feed(manager, _alert(), 0)

    assert all(spy.events == ["send_alert"] for spy in spies)


@pytest.mark.asyncio
async def test_a_channel_registered_later_starts_receiving_events():
    manager = NotificationManager(_config())
    await _feed(manager, _alert(), 0)

    late = RecordingNotifier()
    manager.register("late", late)
    await _feed(manager, _ok(), 1)

    assert late.events == ["send_recovery"]


# ---------------------------------------------------------------------------
# Isolation — the defect this refactor removes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failing_channel_does_not_silence_the_others():
    manager = NotificationManager(_config())
    healthy = RecordingNotifier()
    manager.register("broken", ExplodingNotifier())
    manager.register("healthy", healthy)

    await _feed(manager, _alert(), 0)

    assert healthy.events == ["send_alert"]


@pytest.mark.asyncio
async def test_a_failing_channel_does_not_silence_a_channel_registered_before_it():
    """Order must not matter: the healthy channel goes first here."""
    manager = NotificationManager(_config())
    healthy = RecordingNotifier()
    manager.register("healthy", healthy)
    manager.register("broken", ExplodingNotifier())

    await _feed(manager, _alert(), 0)

    assert healthy.events == ["send_alert"]


@pytest.mark.asyncio
async def test_a_failing_channel_is_logged_with_its_name_and_cause():
    manager = NotificationManager(_config())
    manager.register("broken", ExplodingNotifier())
    logger = MagicMock(spec=logging.Logger)

    await _feed(manager, _alert(), 0, logger=logger)

    failures = [
        call for call in logger.error.call_args_list
        if call[0][0] == "notification channel failed"
    ]
    assert len(failures) == 1
    extra = failures[0][1]["extra"]
    assert extra["target"] == "broken"
    assert extra["event"] == "notify_error"
    assert "send_alert" in extra["reason"]
    assert "channel is broken" in extra["reason"]


@pytest.mark.asyncio
async def test_a_failing_channel_does_not_abort_the_state_machine():
    """The alert must still be recorded, so the later OK is still a transition."""
    manager = NotificationManager(_config())
    healthy = RecordingNotifier()
    manager.register("broken", ExplodingNotifier())
    manager.register("healthy", healthy)

    await _feed(manager, _alert(), 0)
    await _feed(manager, _ok(), 1)

    assert healthy.events == ["send_alert", "send_recovery"]


@pytest.mark.asyncio
async def test_every_event_type_survives_a_failing_channel():
    manager = NotificationManager(_config(threshold=1))
    healthy = RecordingNotifier()
    manager.register("broken", ExplodingNotifier())
    manager.register("healthy", healthy)

    await _feed(manager, _alert(), 0)
    await _feed(manager, _error(), 1)
    await _feed(manager, _ok(), 2)

    assert set(healthy.events) == set(NOTIFIER_METHODS)


# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assigning_the_legacy_attribute_registers_the_channel():
    manager = NotificationManager(_config())
    spy = RecordingNotifier()
    manager.telegram_notifier = spy

    await _feed(manager, _alert(), 0)

    assert spy.events == ["send_alert"]
    assert manager.telegram_notifier is spy


def test_clearing_the_legacy_attribute_unregisters_the_channel():
    manager = NotificationManager(_config(telegram=True))
    assert manager.has_notifiers() is True
    manager.telegram_notifier = None
    assert manager.has_notifiers() is False
    assert manager.telegram_notifier is None


def test_legacy_attributes_are_none_when_the_channel_is_disabled():
    manager = NotificationManager(_config())
    assert manager.telegram_notifier is None
    assert manager.webhook_notifier is None


@pytest.mark.asyncio
async def test_the_two_legacy_attributes_address_distinct_channels():
    manager = NotificationManager(_config())
    telegram, webhook = RecordingNotifier("t"), RecordingNotifier("w")
    manager.telegram_notifier = telegram
    manager.webhook_notifier = webhook

    await _feed(manager, _alert(), 0)

    assert telegram.events == ["send_alert"]
    assert webhook.events == ["send_alert"]
    assert manager.telegram_notifier is not manager.webhook_notifier
