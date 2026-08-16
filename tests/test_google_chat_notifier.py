"""Tests for the Google Chat channel.

Two hazards drive most of this file. The webhook URL is a credential — it carries
`key` and `token` in the query string — and the natural place to leak it is the error
log. And a Chat space accepts one request per second, while a per-service module can
emit a dozen notifications in a single cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    GoogleChatConfig,
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
    load_app_config,
)
from app.core.notifications import NotificationManager
from app.core.types import NOTIFIER_METHODS, MonitorResult, MonitorStatus, Notifier
from app.notifications.google_chat.notifier import (
    GoogleChatNotifier,
    _space_id,
    _thread_key,
    _with_reply_option,
)

WEBHOOK = "https://chat.googleapis.com/v1/spaces/AAQAtjsc1Dk/messages?key=SECRETKEY&token=SECRETTOKEN"
_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _config(*, url=WEBHOOK, interval=0.0, thread=True, enabled=True):
    return GoogleChatConfig(
        enabled=enabled,
        webhook_url=url,
        min_interval_seconds=interval,
        thread_by_check=thread,
    )


def _client(status=200, text="{}", raises=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    client = MagicMock()
    client.post = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
    return client


def _result(status=MonitorStatus.ALERT, items=None, payload=None, message="rockstar status degraded"):
    return MonitorResult(
        status=status,
        message=message,
        reason=", ".join(items) if items else "FiveM: down",
        duration_ms=100.0,
        payload=payload,
        reason_items=items,
    )


def _send(notifier, method="send_alert", *, result=None, client=None, logger=None, module_id="rockstar"):
    client = client or _client()
    logger = logger or MagicMock(spec=logging.Logger)
    asyncio.run(
        getattr(notifier, method)(
            module_id=module_id,
            result=result or _result(),
            interval_seconds=60,
            level_name="WARNING",
            event_name="monitor_check",
            event_time=_NOW,
            http_client=client,
            logger=logger,
        )
    )
    return client, logger


def _body(client):
    return client.post.call_args[1]["json"]


def _card(client):
    return _body(client)["cardsV2"][0]["card"]


def _widget_texts(client):
    texts = []
    for widget in _card(client)["sections"][0]["widgets"]:
        for key in ("textParagraph", "decoratedText"):
            if key in widget:
                texts.append(widget[key]["text"])
    return texts


# ---------------------------------------------------------------------------
# The four events, each visually distinct
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,expected_title",
    [
        ("send_alert", "🚨 Service alert"),
        ("send_recovery", "✅ Service recovered"),
        ("send_monitor_error", "🛑 Monitoring failure"),
        ("send_monitor_recovered", "🔄 Monitoring restored"),
    ],
)
def test_each_event_has_its_own_header(method, expected_title):
    client, _ = _send(GoogleChatNotifier(_config()), method)
    assert _card(client)["header"]["title"] == expected_title


def test_the_four_headers_are_all_different():
    titles = set()
    for method in NOTIFIER_METHODS:
        client, _ = _send(GoogleChatNotifier(_config()), method)
        titles.add(_card(client)["header"]["title"])
    assert len(titles) == 4


def test_monitoring_failure_says_it_is_about_the_checker():
    """"The service is down" and "I cannot check" are different pages."""
    client, _ = _send(GoogleChatNotifier(_config()), "send_monitor_error")
    subtitle = _card(client)["header"]["subtitle"]
    assert "cannot reach" in subtitle
    assert "may be fine" in subtitle


def test_the_module_is_named_in_the_card():
    client, _ = _send(GoogleChatNotifier(_config()))
    assert "rockstar" in _widget_texts(client)


# ---------------------------------------------------------------------------
# One entry per incident
# ---------------------------------------------------------------------------

def test_three_incidents_render_three_entries():
    items = ["FiveM: down", "RedM: down", "Launcher: degraded"]
    client, _ = _send(GoogleChatNotifier(_config()), result=_result(items=items))
    bullets = [t for t in _widget_texts(client) if t.startswith("•")]
    assert len(bullets) == 3


def test_each_entry_carries_one_incident():
    items = ["FiveM: down", "RedM: down"]
    client, _ = _send(GoogleChatNotifier(_config()), result=_result(items=items))
    bullets = [t for t in _widget_texts(client) if t.startswith("•")]
    assert bullets == ["• FiveM: down", "• RedM: down"]


@pytest.mark.parametrize("punctuation", [",", ";", "|"])
def test_an_incident_containing_a_separator_stays_one_entry(punctuation):
    text = f"Elevated errors{punctuation} degraded pushes"
    client, _ = _send(GoogleChatNotifier(_config()), result=_result(items=[text]))
    bullets = [t for t in _widget_texts(client) if t.startswith("•")]
    assert bullets == [f"• {text}"]


def test_falls_back_to_reason_when_the_monitor_sent_no_list():
    result = MonitorResult(MonitorStatus.ALERT, "m", "single incident", 1.0, None)
    client, _ = _send(GoogleChatNotifier(_config()), result=result)
    bullets = [t for t in _widget_texts(client) if t.startswith("•")]
    assert bullets == ["• single incident"]


def test_blank_entries_are_dropped():
    client, _ = _send(
        GoogleChatNotifier(_config()), result=_result(items=["a", "", "   ", "b"])
    )
    bullets = [t for t in _widget_texts(client) if t.startswith("•")]
    assert bullets == ["• a", "• b"]


# ---------------------------------------------------------------------------
# Escaping — a card text field renders HTML
# ---------------------------------------------------------------------------

def test_html_in_provider_text_is_escaped():
    client, _ = _send(
        GoogleChatNotifier(_config()), result=_result(items=["<b>bold</b> & <i>more</i>"])
    )
    bullet = [t for t in _widget_texts(client) if t.startswith("•")][0]
    assert "<b>" not in bullet
    assert "&lt;b&gt;" in bullet
    assert "&amp;" in bullet


def test_a_script_tag_cannot_survive_into_the_card():
    client, _ = _send(
        GoogleChatNotifier(_config()), result=_result(items=["<script>alert(1)</script>"])
    )
    assert "<script>" not in json.dumps(_body(client))


def test_the_module_name_is_escaped_in_display_text():
    client, _ = _send(GoogleChatNotifier(_config()), module_id="<b>evil</b>")
    assert "&lt;b&gt;evil&lt;/b&gt;" in _widget_texts(client)


def test_markup_never_reaches_the_card_id_or_the_thread_key():
    """These are keys, not display text — they get slugified, not HTML-escaped."""
    client, _ = _send(GoogleChatNotifier(_config()), module_id="<b>evil</b>")
    body = _body(client)
    assert body["cardsV2"][0]["cardId"] == "service-checker-b-evil-b-alert"
    assert body["thread"]["threadKey"] == "b-evil-b"
    assert "<b>" not in json.dumps(body)


def test_an_untrusted_component_id_is_slugified_into_the_thread_key():
    """The component half comes from the provider payload."""
    result = _result(payload=[{"id": "US East (Ashburn) | 210f910e"}])
    assert _thread_key("oci", result) == "oci:us-east-ashburn-210f910e"


def test_chat_markup_characters_are_left_literal():
    """Cards render HTML, not Chat markup, so asterisks must survive as themselves."""
    client, _ = _send(GoogleChatNotifier(_config()), result=_result(items=["a *b* _c_ `d`"]))
    bullet = [t for t in _widget_texts(client) if t.startswith("•")][0]
    assert bullet == "• a *b* _c_ `d`"


# ---------------------------------------------------------------------------
# The credential must never reach a log line
# ---------------------------------------------------------------------------

def _all_log_text(logger):
    chunks = []
    for method in ("debug", "info", "warning", "error"):
        for call in getattr(logger, method).call_args_list:
            chunks.append(str(call))
    return " ".join(chunks)


@pytest.mark.parametrize(
    "client_kwargs",
    [
        {"status": 429, "text": "RESOURCE_EXHAUSTED"},
        {"status": 403, "text": "PERMISSION_DENIED"},
        {"status": 500, "text": "internal"},
        {"raises": httpx_timeout} if (httpx_timeout := None) else {"raises": TimeoutError("timed out")},
    ],
)
def test_the_webhook_url_never_reaches_a_log_line(client_kwargs):
    client = _client(**client_kwargs)
    _, logger = _send(GoogleChatNotifier(_config()), client=client)
    text = _all_log_text(logger)
    assert WEBHOOK not in text
    assert "SECRETKEY" not in text
    assert "key=" not in text
    assert "token=" not in text


def test_the_url_is_absent_from_the_success_log_too():
    _, logger = _send(GoogleChatNotifier(_config()))
    text = _all_log_text(logger)
    assert "SECRETTOKEN" not in text
    assert "AAQAtjsc1Dk" in text  # the space id is safe and useful


def test_space_id_extracts_only_the_path_segment():
    assert _space_id(WEBHOOK) == "AAQAtjsc1Dk"


def test_space_id_is_unknown_for_an_unexpected_url():
    assert _space_id("https://example.com/nope") == "unknown"
    assert _space_id(None) == "unknown"


# ---------------------------------------------------------------------------
# Failure paths degrade safely
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_an_error_response_is_logged_without_raising(status):
    client = _client(status=status, text="boom")
    _, logger = _send(GoogleChatNotifier(_config()), client=client)
    assert logger.error.call_count == 1
    assert str(status) in str(logger.error.call_args)


def test_the_error_body_is_logged_for_diagnosis():
    client = _client(status=400, text="INVALID_ARGUMENT: cards is malformed")
    _, logger = _send(GoogleChatNotifier(_config()), client=client)
    assert "INVALID_ARGUMENT" in str(logger.error.call_args)


def test_a_rate_limit_is_not_retried():
    """Pacing is the prevention; retrying inside a throttled channel stacks delay."""
    client = _client(status=429, text="RESOURCE_EXHAUSTED")
    _send(GoogleChatNotifier(_config()), client=client)
    assert client.post.call_count == 1


def test_a_network_failure_does_not_raise():
    client = _client(raises=OSError("connection reset"))
    _, logger = _send(GoogleChatNotifier(_config()), client=client)
    assert logger.error.call_count == 1
    assert "connection reset" in str(logger.error.call_args)


def test_a_missing_url_skips_without_posting():
    client = _client()
    _, logger = _send(GoogleChatNotifier(_config(url=None)), client=client)
    client.post.assert_not_called()
    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Quota pacing
# ---------------------------------------------------------------------------

def test_consecutive_sends_are_paced():
    notifier = GoogleChatNotifier(_config(interval=0.15))
    client = _client()
    started = time.monotonic()
    for _ in range(3):
        _send(notifier, client=client)
    elapsed = time.monotonic() - started
    # First send is immediate; the two that follow each wait out the interval.
    assert elapsed >= 0.15
    assert client.post.call_count == 3


def test_pacing_can_be_disabled():
    notifier = GoogleChatNotifier(_config(interval=0.0))
    started = time.monotonic()
    for _ in range(5):
        _send(notifier)
    assert time.monotonic() - started < 0.5


def test_a_failed_send_does_not_start_the_pacing_clock():
    """A request that never reached the space did not consume the space's quota."""
    notifier = GoogleChatNotifier(_config(interval=5.0))
    _send(notifier, client=_client(raises=OSError("down")))
    started = time.monotonic()
    _send(notifier)
    assert time.monotonic() - started < 1.0


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------

def test_thread_key_is_the_component_when_the_payload_has_one():
    result = _result(payload=[{"id": "fivem", "name": "FiveM"}])
    assert _thread_key("rockstar", result) == "rockstar:fivem"


def test_thread_key_falls_back_to_the_module():
    assert _thread_key("rockstar", _result(payload={"hero": "x"})) == "rockstar"


def test_thread_key_is_identical_for_an_alert_and_its_recovery():
    alert = _result(payload=[{"id": "fivem", "name": "FiveM"}])
    recovery = _result(
        status=MonitorStatus.OK, payload=[{"id": "fivem", "name": "FiveM", "status": "operational"}]
    )
    assert _thread_key("rockstar", alert) == _thread_key("rockstar", recovery)


def test_the_thread_travels_in_the_body_and_the_reply_option_in_the_url():
    client, _ = _send(
        GoogleChatNotifier(_config(thread=True)),
        result=_result(payload=[{"id": "fivem"}]),
    )
    assert _body(client)["thread"] == {"threadKey": "rockstar:fivem"}
    assert "messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" in client.post.call_args[0][0]


def test_threading_disabled_sends_no_thread_and_no_reply_option():
    client, _ = _send(GoogleChatNotifier(_config(thread=False)))
    assert "thread" not in _body(client)
    assert "messageReplyOption" not in client.post.call_args[0][0]


def test_the_reply_option_is_not_appended_twice():
    url = WEBHOOK + "&messageReplyOption=REPLY_MESSAGE_OR_FAIL"
    assert _with_reply_option(url) == url


def test_the_reply_option_uses_a_question_mark_when_there_is_no_query():
    assert _with_reply_option("https://chat.googleapis.com/v1/spaces/S/messages").endswith(
        "?messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    )


# ---------------------------------------------------------------------------
# Wiring into the manager
# ---------------------------------------------------------------------------

def _notification_config(google_chat_enabled):
    return NotificationConfig(
        telegram=TelegramConfig(False, None, [], "", "", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10,
        error_threshold=3,
        google_chat=_config(enabled=google_chat_enabled),
    )


def test_the_channel_satisfies_the_notifier_protocol():
    notifier = GoogleChatNotifier(_config())
    assert isinstance(notifier, Notifier)
    for method in NOTIFIER_METHODS:
        assert callable(getattr(notifier, method))


def test_the_manager_registers_it_when_enabled():
    manager = NotificationManager(_notification_config(True))
    assert manager.has_notifiers() is True
    assert "google_chat" in manager._notifiers


def test_the_manager_skips_it_when_disabled():
    manager = NotificationManager(_notification_config(False))
    assert "google_chat" not in manager._notifiers


def test_register_accepts_it():
    manager = NotificationManager(_notification_config(False))
    manager.register("google_chat", GoogleChatNotifier(_config()))
    assert "google_chat" in manager._notifiers


@pytest.mark.asyncio
async def test_the_channel_receives_an_event_through_the_manager():
    manager = NotificationManager(_notification_config(True))
    client = _client()
    await manager.handle_result(
        module_id="rockstar",
        result=_result(payload={"hero": "x"}),
        module_config=ModuleConfig(
            "rockstar", "http://x", 60, 10.0, "ua", RuleConfig("status", "major"), [], True
        ),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_NOW,
        http_client=client,
        logger=MagicMock(spec=logging.Logger),
    )
    assert client.post.call_count == 1
    assert _card(client)["header"]["title"] == "🚨 Service alert"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_the_channel_is_disabled_by_default(monkeypatch):
    for key in (
        "GOOGLE_CHAT_ENABLED",
        "GOOGLE_CHAT_WEBHOOK_URL",
        "GOOGLE_CHAT_THREAD_BY_CHECK",
        "GOOGLE_CHAT_MIN_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    google_chat = load_app_config().notifications.google_chat
    assert google_chat.enabled is False
    assert google_chat.webhook_url is None


def test_defaults_pace_below_the_quota(monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_MIN_INTERVAL_SECONDS", raising=False)
    assert load_app_config().notifications.google_chat.min_interval_seconds >= 1.0


def test_threading_defaults_to_on(monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_THREAD_BY_CHECK", raising=False)
    assert load_app_config().notifications.google_chat.thread_by_check is True


def test_env_overrides_are_read(monkeypatch):
    monkeypatch.setenv("GOOGLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("GOOGLE_CHAT_MIN_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("GOOGLE_CHAT_THREAD_BY_CHECK", "false")
    google_chat = load_app_config().notifications.google_chat
    assert google_chat.enabled is True
    assert google_chat.webhook_url == WEBHOOK
    assert google_chat.min_interval_seconds == 2.5
    assert google_chat.thread_by_check is False


def test_a_negative_interval_is_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("GOOGLE_CHAT_MIN_INTERVAL_SECONDS", "-5")
    assert load_app_config().notifications.google_chat.min_interval_seconds == 0.0
