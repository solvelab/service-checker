"""Tests for the Alertmanager channel.

The hazard here is not transport, it is semantics. Alertmanager expects firing alerts to
be retransmitted, and resolves them on its own once `endsAt` elapses. The Service Checker
suppresses repeats inside `NOTIFICATION_REPEAT_MINUTES`, and does so before any channel is
called. Get the margin wrong and the alert flaps: self-resolved at the timeout, recreated
at the next send, forever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    AlertmanagerConfig,
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
    load_app_config,
)
from app.core.notifications import NotificationManager
from app.core.types import NOTIFIER_METHODS, MonitorResult, MonitorStatus, Notifier
from app.notifications.alertmanager.notifier import (
    ALERTNAME_MONITORING,
    ALERTNAME_SERVICE,
    RESERVED_LABELS,
    AlertmanagerNotifier,
)

_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")


def _config(*, url="http://alertmanager:9093", token=None, resolve_after=0.0,
            extra=None, repeat_minutes=10, enabled=True):
    return AlertmanagerConfig(
        enabled=enabled,
        url=url,
        token=token,
        header_name="Authorization",
        resolve_after_seconds=resolve_after,
        extra_labels=extra or {},
        repeat_minutes=repeat_minutes,
    )


def _client(status=200, text="{}", raises=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    client = MagicMock()
    client.post = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
    return client


def _result(items=None, payload=None, reason="API: major_outage", message="github status degraded"):
    return MonitorResult(
        status=MonitorStatus.ALERT,
        message=message,
        reason=reason,
        duration_ms=100.0,
        payload=payload,
        reason_items=items,
    )


def _send(notifier, method="send_alert", *, result=None, client=None, logger=None,
          module_id="github", interval=60, when=None):
    client = client or _client()
    logger = logger or MagicMock(spec=logging.Logger)
    asyncio.run(
        getattr(notifier, method)(
            module_id=module_id,
            result=result if result is not None else _result(),
            interval_seconds=interval,
            level_name="WARNING",
            event_name="service_alert",
            event_time=when or _NOW,
            http_client=client,
            logger=logger,
        )
    )
    return client, logger


def _alert(client):
    return client.post.call_args[1]["json"][0]


def _seconds_ahead(alert):
    starts = datetime.fromisoformat(alert["startsAt"])
    ends = datetime.fromisoformat(alert["endsAt"])
    return (ends - starts).total_seconds()


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

def test_the_body_is_an_array_even_for_one_alert():
    client, _ = _send(AlertmanagerNotifier(_config()))
    body = client.post.call_args[1]["json"]
    assert isinstance(body, list)
    assert len(body) == 1


def test_it_posts_to_the_v2_alerts_endpoint():
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert client.post.call_args[0][0] == "http://alertmanager:9093/api/v2/alerts"


def test_a_trailing_slash_in_the_base_url_does_not_double_up():
    client, _ = _send(AlertmanagerNotifier(_config(url="http://alertmanager:9093/")))
    assert client.post.call_args[0][0] == "http://alertmanager:9093/api/v2/alerts"


def test_alertname_is_always_present():
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert _alert(client)["labels"]["alertname"] == ALERTNAME_SERVICE


def test_the_token_is_sent_in_the_configured_header():
    client, _ = _send(AlertmanagerNotifier(_config(token="Bearer abc")))
    assert client.post.call_args[1]["headers"]["Authorization"] == "Bearer abc"


def test_no_auth_header_when_no_token():
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert client.post.call_args[1]["headers"] == {}


@pytest.mark.parametrize("field", ["startsAt", "endsAt"])
def test_timestamps_are_rfc3339_with_an_offset(field):
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert _RFC3339.match(_alert(client)[field]), _alert(client)[field]


# ---------------------------------------------------------------------------
# Firing vs resolved
# ---------------------------------------------------------------------------

def test_a_firing_alert_ends_in_the_future():
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert _seconds_ahead(_alert(client)) > 0


@pytest.mark.parametrize("method", ["send_recovery", "send_monitor_recovered"])
def test_a_recovery_ends_in_the_past(method):
    client, _ = _send(AlertmanagerNotifier(_config()), method)
    assert _seconds_ahead(_alert(client)) < 0


def test_the_margin_exceeds_the_real_gap_between_two_sends():
    """The resend lands on the first cycle at or after the repeat window elapses."""
    repeat_minutes, interval = 10, 60
    client, _ = _send(
        AlertmanagerNotifier(_config(repeat_minutes=repeat_minutes)), interval=interval
    )
    worst_case_gap = repeat_minutes * 60 + interval
    assert _seconds_ahead(_alert(client)) > worst_case_gap


@pytest.mark.parametrize("repeat_minutes,interval", [(1, 60), (10, 60), (30, 300), (60, 900)])
def test_no_flapping_across_plausible_configurations(repeat_minutes, interval):
    client, _ = _send(
        AlertmanagerNotifier(_config(repeat_minutes=repeat_minutes)), interval=interval
    )
    assert _seconds_ahead(_alert(client)) > repeat_minutes * 60 + interval


def test_the_margin_never_drops_below_the_alertmanager_default_timeout():
    """Alertmanager's own resolve_timeout defaults to 5 minutes."""
    client, _ = _send(AlertmanagerNotifier(_config(repeat_minutes=1)), interval=1)
    assert _seconds_ahead(_alert(client)) >= 300


def test_an_explicit_margin_overrides_the_derived_one():
    client, _ = _send(AlertmanagerNotifier(_config(resolve_after=4242.0)))
    assert _seconds_ahead(_alert(client)) == 4242.0


def test_a_longer_repeat_window_widens_the_margin():
    short, _ = _send(AlertmanagerNotifier(_config(repeat_minutes=10)))
    long, _ = _send(AlertmanagerNotifier(_config(repeat_minutes=60)))
    assert _seconds_ahead(_alert(long)) > _seconds_ahead(_alert(short))


# ---------------------------------------------------------------------------
# Identity: stable, distinct, deduplicable
# ---------------------------------------------------------------------------

def test_labels_are_identical_across_two_cycles_of_the_same_incident():
    """A varying label set would create a new alert each cycle instead of deduplicating."""
    notifier = AlertmanagerNotifier(_config())
    payload = [{"id": "api", "name": "API"}]
    first, _ = _send(notifier, result=_result(payload=payload), when=_NOW)
    second, _ = _send(
        notifier, result=_result(payload=payload), when=_NOW + timedelta(minutes=10)
    )
    assert _alert(first)["labels"] == _alert(second)["labels"]


def test_two_components_produce_two_distinct_label_sets():
    notifier = AlertmanagerNotifier(_config())
    api, _ = _send(notifier, result=_result(payload=[{"id": "api"}]))
    actions, _ = _send(notifier, result=_result(payload=[{"id": "actions"}]))
    assert _alert(api)["labels"] != _alert(actions)["labels"]
    assert _alert(api)["labels"]["check_id"] == "github:api"
    assert _alert(actions)["labels"]["check_id"] == "github:actions"


def test_alertname_does_not_vary_with_the_incident():
    notifier = AlertmanagerNotifier(_config())
    api, _ = _send(notifier, result=_result(payload=[{"id": "api"}]))
    actions, _ = _send(notifier, result=_result(payload=[{"id": "actions"}]))
    assert _alert(api)["labels"]["alertname"] == _alert(actions)["labels"]["alertname"]


def test_a_module_level_alert_keys_on_the_module():
    client, _ = _send(AlertmanagerNotifier(_config()), module_id="rockstar",
                      result=_result(payload={"hero": "x"}))
    labels = _alert(client)["labels"]
    assert labels["check_id"] == "rockstar"
    assert "component" not in labels


def test_monitoring_failure_is_distinguishable_by_label():
    notifier = AlertmanagerNotifier(_config())
    service, _ = _send(notifier, "send_alert")
    monitoring, _ = _send(notifier, "send_monitor_error")
    assert _alert(service)["labels"]["alertname"] == ALERTNAME_SERVICE
    assert _alert(monitoring)["labels"]["alertname"] == ALERTNAME_MONITORING
    assert _alert(monitoring)["labels"]["severity"] == "critical"


def test_every_alert_is_attributable_to_this_source():
    client, _ = _send(AlertmanagerNotifier(_config()))
    assert _alert(client)["labels"]["source"] == "service-checker"


# ---------------------------------------------------------------------------
# Cardinality: free text stays out of labels
# ---------------------------------------------------------------------------

def test_the_reason_is_an_annotation_not_a_label():
    reason = "Frankfurt / AWS Direct Connect: Increased Packet loss"
    client, _ = _send(AlertmanagerNotifier(_config()), result=_result(reason=reason))
    alert = _alert(client)
    assert alert["annotations"]["summary"] == reason
    assert reason not in alert["labels"].values()


def test_the_message_is_an_annotation_not_a_label():
    client, _ = _send(AlertmanagerNotifier(_config()), result=_result(message="a long message"))
    alert = _alert(client)
    assert alert["annotations"]["description"] == "a long message"
    assert "a long message" not in alert["labels"].values()


def test_the_incident_list_travels_as_an_annotation():
    items = ["API: major_outage", "Actions: partial_outage"]
    client, _ = _send(AlertmanagerNotifier(_config()), result=_result(items=items))
    assert _alert(client)["annotations"]["incidents"] == "API: major_outage\nActions: partial_outage"


def test_label_values_stay_short_and_bounded():
    """Long free text in a label is the classic cardinality blowout."""
    client, _ = _send(
        AlertmanagerNotifier(_config()), result=_result(reason="x" * 500, message="y" * 500)
    )
    assert all(len(value) < 100 for value in _alert(client)["labels"].values())


# ---------------------------------------------------------------------------
# Static routing labels
# ---------------------------------------------------------------------------

def test_static_labels_are_merged_into_every_alert():
    client, _ = _send(
        AlertmanagerNotifier(_config(extra={"env": "prod", "cluster": "main"}))
    )
    labels = _alert(client)["labels"]
    assert labels["env"] == "prod"
    assert labels["cluster"] == "main"


@pytest.mark.parametrize("reserved", sorted(RESERVED_LABELS))
def test_static_labels_cannot_hijack_the_alert_identity(reserved):
    client, _ = _send(
        AlertmanagerNotifier(_config(extra={reserved: "hijacked"})),
        result=_result(payload=[{"id": "api"}]),
    )
    assert _alert(client)["labels"].get(reserved) != "hijacked"


def test_no_static_labels_configured_is_fine():
    client, _ = _send(AlertmanagerNotifier(_config(extra={})))
    assert _alert(client)["labels"]["alertname"] == ALERTNAME_SERVICE


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_an_error_response_is_logged_without_raising(status):
    client = _client(status=status, text="rejected")
    _, logger = _send(AlertmanagerNotifier(_config()), client=client)
    assert logger.error.call_count == 1
    assert str(status) in str(logger.error.call_args)


def test_a_network_failure_does_not_raise():
    client = _client(raises=OSError("connection refused"))
    _, logger = _send(AlertmanagerNotifier(_config()), client=client)
    assert logger.error.call_count == 1
    assert "connection refused" in str(logger.error.call_args)


def test_a_missing_url_skips_without_posting():
    client = _client()
    _, logger = _send(AlertmanagerNotifier(_config(url=None)), client=client)
    client.post.assert_not_called()
    logger.warning.assert_called_once()


def test_the_token_never_reaches_a_log_line():
    client = _client(status=500, text="boom")
    _, logger = _send(AlertmanagerNotifier(_config(token="Bearer SUPERSECRET")), client=client)
    text = " ".join(
        str(call)
        for method in ("debug", "info", "warning", "error")
        for call in getattr(logger, method).call_args_list
    )
    assert "SUPERSECRET" not in text


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def _notification_config(enabled, repeat_minutes=10):
    return NotificationConfig(
        telegram=TelegramConfig(False, None, [], "", "", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=repeat_minutes,
        error_threshold=3,
        alertmanager=_config(enabled=enabled, repeat_minutes=repeat_minutes),
    )


def test_the_channel_satisfies_the_notifier_protocol():
    notifier = AlertmanagerNotifier(_config())
    assert isinstance(notifier, Notifier)
    for method in NOTIFIER_METHODS:
        assert callable(getattr(notifier, method))


def test_the_manager_registers_it_when_enabled():
    manager = NotificationManager(_notification_config(True))
    assert "alertmanager" in manager._notifiers


def test_the_manager_skips_it_when_disabled():
    manager = NotificationManager(_notification_config(False))
    assert "alertmanager" not in manager._notifiers


@pytest.mark.asyncio
async def test_the_channel_receives_an_event_through_the_manager():
    manager = NotificationManager(_notification_config(True))
    client = _client()
    await manager.handle_result(
        module_id="github",
        result=_result(payload=[{"id": "api", "name": "API"}]),
        module_config=ModuleConfig(
            "github", "http://x", 60, 10.0, "ua", RuleConfig("status", "major_outage"), [], True
        ),
        level_name="WARNING",
        event_name="service_alert",
        event_time=_NOW,
        http_client=client,
        logger=MagicMock(spec=logging.Logger),
    )
    assert client.post.call_count == 1
    assert _alert(client)["labels"]["check_id"] == "github:api"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _clear(monkeypatch):
    for key in (
        "ALERTMANAGER_ENABLED",
        "ALERTMANAGER_URL",
        "ALERTMANAGER_TOKEN",
        "ALERTMANAGER_HEADER_NAME",
        "ALERTMANAGER_RESOLVE_AFTER_SECONDS",
        "ALERTMANAGER_EXTRA_LABELS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_the_channel_is_disabled_by_default(monkeypatch):
    _clear(monkeypatch)
    alertmanager = load_app_config().notifications.alertmanager
    assert alertmanager.enabled is False
    assert alertmanager.url is None


def test_the_repeat_window_is_mirrored_into_the_channel(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("NOTIFICATION_REPEAT_MINUTES", "25")
    notifications = load_app_config().notifications
    assert notifications.alertmanager.repeat_minutes == notifications.repeat_minutes == 25


def test_extra_labels_are_parsed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALERTMANAGER_EXTRA_LABELS", "env=prod,cluster=main")
    assert load_app_config().notifications.alertmanager.extra_labels == {
        "env": "prod",
        "cluster": "main",
    }


def test_extra_labels_tolerate_whitespace(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALERTMANAGER_EXTRA_LABELS", " env = prod , cluster = main ")
    assert load_app_config().notifications.alertmanager.extra_labels == {
        "env": "prod",
        "cluster": "main",
    }


@pytest.mark.parametrize("raw", ["", "novalue", "=orphan", "k=", ",,,"])
def test_malformed_extra_labels_do_not_break_startup(monkeypatch, raw):
    """A typo in a routing label must not stop the service from starting."""
    _clear(monkeypatch)
    monkeypatch.setenv("ALERTMANAGER_EXTRA_LABELS", raw)
    assert load_app_config().notifications.alertmanager.extra_labels == {}


def test_valid_pairs_survive_alongside_malformed_ones(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALERTMANAGER_EXTRA_LABELS", "env=prod,garbage,cluster=main")
    assert load_app_config().notifications.alertmanager.extra_labels == {
        "env": "prod",
        "cluster": "main",
    }


def test_a_negative_margin_is_clamped_to_auto(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALERTMANAGER_RESOLVE_AFTER_SECONDS", "-10")
    assert load_app_config().notifications.alertmanager.resolve_after_seconds == 0.0


def test_the_alert_body_is_json_serialisable():
    client, _ = _send(AlertmanagerNotifier(_config(extra={"env": "prod"})))
    json.dumps(client.post.call_args[1]["json"])
