"""Tests for monitoring-failure notifications (change add-monitoring-failure-notifications).

A monitor that cannot reach its upstream used to be completely silent: ERROR notified
nothing, so a dead monitor and a healthy one looked the same in the notification channel.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
    load_app_config,
)
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _config(*, threshold=3, repeat_minutes=10, telegram=True, webhook=False):
    return NotificationConfig(
        telegram=TelegramConfig(
            enabled=telegram,
            bot_token="tok",
            chat_ids=["123"],
            api_url="https://api.telegram.org",
            timestamp_format="%Y-%m-%d %H:%M:%S %Z",
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


def _manager(**kwargs):
    manager = NotificationManager(_config(**kwargs))
    for attr in ("telegram_notifier", "webhook_notifier"):
        notifier = getattr(manager, attr)
        if notifier is None:
            continue
        stub = MagicMock()
        for method in (
            "send_alert",
            "send_recovery",
            "send_monitor_error",
            "send_monitor_recovered",
        ):
            setattr(stub, method, AsyncMock(return_value=True))
        setattr(manager, attr, stub)
    return manager


def _module_config(slug="rockstar"):
    return ModuleConfig(
        slug=slug,
        url="https://example.com",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value="major,minor"),
        service_filter=[],
        enabled=True,
    )


_T0 = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def _at(minutes):
    return _T0 + timedelta(minutes=minutes)


def _error(reason="OSError: network unreachable"):
    return MonitorResult(MonitorStatus.ERROR, "request failed", reason, 50.0, None)


def _ok():
    return MonitorResult(MonitorStatus.OK, "healthy", None, 80.0, {"hero": "ok"})


def _alert(reason="FiveM: There are partial outages"):
    return MonitorResult(MonitorStatus.ALERT, "degraded", reason, 90.0, {"hero": "bad"})


async def _feed(manager, result, when, module_id="rockstar", logger=None):
    await manager.handle_result(
        module_id=module_id,
        result=result,
        module_config=_module_config(module_id),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=when,
        http_client=AsyncMock(),
        logger=logger or MagicMock(spec=logging.Logger),
    )


# ---------------------------------------------------------------------------
# 5.1 Threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_errors_below_threshold_stay_silent():
    manager = _manager(threshold=3)
    for minute in range(2):
        await _feed(manager, _error(), _at(minute))
    manager.telegram_notifier.send_monitor_error.assert_not_called()


@pytest.mark.asyncio
async def test_threshold_reached_notifies_exactly_once():
    manager = _manager(threshold=3)
    for minute in range(3):
        await _feed(manager, _error(), _at(minute))
    manager.telegram_notifier.send_monitor_error.assert_called_once()


@pytest.mark.asyncio
async def test_failure_notification_carries_count_and_last_reason():
    manager = _manager(threshold=3)
    await _feed(manager, _error("timeout"), _at(0))
    await _feed(manager, _error("timeout"), _at(1))
    await _feed(manager, _error("HTTP 403"), _at(2))

    reason = manager.telegram_notifier.send_monitor_error.call_args[1]["result"].reason
    assert "3 consecutive failed checks" in reason
    assert "HTTP 403" in reason


# ---------------------------------------------------------------------------
# 5.2 Throttle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sustained_error_respects_the_repeat_window():
    manager = _manager(threshold=3, repeat_minutes=10)
    for minute in range(60):
        await _feed(manager, _error(), _at(minute))

    # First at minute 2 (threshold), then every 10 minutes: 2, 12, 22, 32, 42, 52.
    assert manager.telegram_notifier.send_monitor_error.call_count == 6


@pytest.mark.asyncio
async def test_sustained_error_does_not_notify_every_cycle():
    manager = _manager(threshold=1, repeat_minutes=10)
    for minute in range(10):
        await _feed(manager, _error(), _at(minute))
    assert manager.telegram_notifier.send_monitor_error.call_count == 1


# ---------------------------------------------------------------------------
# 5.3 / 5.4 Recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notified_failure_then_ok_announces_monitoring_restored():
    manager = _manager(threshold=3)
    for minute in range(3):
        await _feed(manager, _error(), _at(minute))
    await _feed(manager, _ok(), _at(3))

    manager.telegram_notifier.send_monitor_recovered.assert_called_once()


@pytest.mark.asyncio
async def test_unannounced_failure_recovers_silently():
    """Nobody was told it broke, so there is nothing to announce as restored."""
    manager = _manager(threshold=3)
    await _feed(manager, _error(), _at(0))
    await _feed(manager, _error(), _at(1))
    await _feed(manager, _ok(), _at(2))

    manager.telegram_notifier.send_monitor_error.assert_not_called()
    manager.telegram_notifier.send_monitor_recovered.assert_not_called()


@pytest.mark.asyncio
async def test_alert_also_ends_a_monitoring_outage():
    """Reaching the upstream and finding it degraded still means monitoring works."""
    manager = _manager(threshold=3)
    for minute in range(3):
        await _feed(manager, _error(), _at(minute))
    await _feed(manager, _alert(), _at(3))

    manager.telegram_notifier.send_monitor_recovered.assert_called_once()
    manager.telegram_notifier.send_alert.assert_called_once()


# ---------------------------------------------------------------------------
# 5.5 Counter reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_counter_resets_on_ok():
    manager = _manager(threshold=3)
    await _feed(manager, _error(), _at(0))
    await _feed(manager, _error(), _at(1))
    await _feed(manager, _ok(), _at(2))
    await _feed(manager, _error(), _at(3))
    await _feed(manager, _error(), _at(4))

    manager.telegram_notifier.send_monitor_error.assert_not_called()


@pytest.mark.asyncio
async def test_counter_resets_on_alert():
    manager = _manager(threshold=3)
    await _feed(manager, _error(), _at(0))
    await _feed(manager, _error(), _at(1))
    await _feed(manager, _alert(), _at(2))
    await _feed(manager, _error(), _at(3))
    await _feed(manager, _error(), _at(4))

    manager.telegram_notifier.send_monitor_error.assert_not_called()


@pytest.mark.asyncio
async def test_second_error_block_must_reach_threshold_again():
    manager = _manager(threshold=3)
    for minute in range(3):
        await _feed(manager, _error(), _at(minute))
    await _feed(manager, _ok(), _at(3))
    for minute in range(4, 7):
        await _feed(manager, _error(), _at(minute))

    assert manager.telegram_notifier.send_monitor_error.call_count == 2


# ---------------------------------------------------------------------------
# 5.6 Composition with the v2.2.3 fix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_alert_survives_a_notified_monitoring_failure():
    """ALERT -> long outage -> OK must emit BOTH recoveries, service one keeping its text."""
    manager = _manager(threshold=3)
    logger = MagicMock(spec=logging.Logger)

    await _feed(manager, _alert("FiveM: There are partial outages"), _at(0), logger=logger)
    for minute in range(1, 31):
        await _feed(manager, _error(), _at(minute), logger=logger)
    await _feed(manager, _ok(), _at(31), logger=logger)

    manager.telegram_notifier.send_monitor_recovered.assert_called_once()
    manager.telegram_notifier.send_recovery.assert_called_once()

    recovery_logs = [
        c for c in logger.info.call_args_list
        if c[0][0] == "recovery notification emitted"
    ]
    assert len(recovery_logs) == 1
    assert recovery_logs[0][1]["extra"]["from_status"] == "FiveM: There are partial outages"


@pytest.mark.asyncio
async def test_monitoring_failure_does_not_reset_the_alert_repeat_window():
    manager = _manager(threshold=3, repeat_minutes=10)
    await _feed(manager, _alert(), _at(0))
    for minute in range(1, 6):
        await _feed(manager, _error(), _at(minute))
    await _feed(manager, _alert(), _at(6))

    assert manager.telegram_notifier.send_alert.call_count == 1


@pytest.mark.asyncio
async def test_monitoring_recovery_is_announced_before_service_recovery():
    manager = _manager(threshold=3)
    order = []
    manager.telegram_notifier.send_monitor_recovered = AsyncMock(
        side_effect=lambda **kw: order.append("monitor")
    )
    manager.telegram_notifier.send_recovery = AsyncMock(
        side_effect=lambda **kw: order.append("service")
    )

    await _feed(manager, _alert(), _at(0))
    for minute in range(1, 4):
        await _feed(manager, _error(), _at(minute))
    await _feed(manager, _ok(), _at(4))

    assert order == ["monitor", "service"]


# ---------------------------------------------------------------------------
# 5.7 Threshold clamping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["0", "-5"])
def test_threshold_below_one_is_clamped(monkeypatch, raw):
    monkeypatch.setenv("NOTIFICATION_ERROR_THRESHOLD", raw)
    assert load_app_config().notifications.error_threshold == 1


def test_threshold_defaults_to_three(monkeypatch):
    monkeypatch.delenv("NOTIFICATION_ERROR_THRESHOLD", raising=False)
    assert load_app_config().notifications.error_threshold == 3


def test_threshold_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_ERROR_THRESHOLD", "not-a-number")
    assert load_app_config().notifications.error_threshold == 3


@pytest.mark.asyncio
async def test_clamped_threshold_notifies_on_the_first_error():
    manager = _manager(threshold=1)
    await _feed(manager, _error(), _at(0))
    manager.telegram_notifier.send_monitor_error.assert_called_once()


# ---------------------------------------------------------------------------
# 5.8 Webhook contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_uses_dedicated_status_values():
    manager = _manager(threshold=1, telegram=False, webhook=True)
    posted = []

    async def capture(**kwargs):
        posted.append(kwargs)
        # side_effect manda no retorno do AsyncMock: sem isto o canal diria que
        # nao entregou, e o estado nao avancaria para o evento seguinte.
        return True

    manager.webhook_notifier.send_monitor_error = AsyncMock(side_effect=capture)
    manager.webhook_notifier.send_monitor_recovered = AsyncMock(side_effect=capture)

    await _feed(manager, _error(), _at(0))
    await _feed(manager, _ok(), _at(1))

    manager.webhook_notifier.send_monitor_error.assert_called_once()
    manager.webhook_notifier.send_monitor_recovered.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_payload_status_strings():
    """The wire contract: MONITOR_ERROR / MONITOR_RECOVERED, never ALERT / RESOLVED."""
    from app.notifications.webhook.notifier import WebhookNotifier

    notifier = WebhookNotifier(
        WebhookConfig(True, "https://hook.example.com", "secret", "Authorization")
    )
    client = MagicMock()
    # O webhook agora le o status da resposta para dizer se entregou; um mock sem
    # status_code nao modela mais um endpoint que aceitou.
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    logger = MagicMock(spec=logging.Logger)
    result = MonitorResult(MonitorStatus.ERROR, "monitoring failure", "3 failed", 10.0)

    await notifier.send_monitor_error(
        module_id="rockstar",
        result=result,
        interval_seconds=60,
        level_name="ERROR",
        event_name="monitor_failure",
        event_time=_at(0),
        http_client=client,
        logger=logger,
    )
    assert client.post.call_args[1]["json"]["status"] == "MONITOR_ERROR"
    assert client.post.call_args[1]["json"]["check_id"] == "rockstar"

    await notifier.send_monitor_recovered(
        module_id="rockstar",
        result=result,
        interval_seconds=60,
        level_name="INFO",
        event_name="monitor_failure_resolved",
        event_time=_at(1),
        http_client=client,
        logger=logger,
    )
    assert client.post.call_args[1]["json"]["status"] == "MONITOR_RECOVERED"


@pytest.mark.asyncio
async def test_webhook_without_url_skips_without_raising():
    from app.notifications.webhook.notifier import WebhookNotifier

    notifier = WebhookNotifier(WebhookConfig(True, None, None, "Authorization"))
    client = MagicMock()
    # O webhook agora le o status da resposta para dizer se entregou; um mock sem
    # status_code nao modela mais um endpoint que aceitou.
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    logger = MagicMock(spec=logging.Logger)

    await notifier.send_monitor_error(
        module_id="rockstar",
        result=MonitorResult(MonitorStatus.ERROR, "x", "y", 1.0),
        interval_seconds=60,
        level_name="ERROR",
        event_name="monitor_failure",
        event_time=_at(0),
        http_client=client,
        logger=logger,
    )
    client.post.assert_not_called()
    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# 5.9 Templates
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent
    / "app" / "notifications" / "telegram" / "templates"
)
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("j2", "html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(name, **kwargs):
    base = {
        "module_id": "rockstar",
        "level": "ERROR",
        "status": "ERROR",
        "message": "monitoring failure",
        "reason": "3 consecutive failed checks; last error: HTTP 403",
        "timestamp": "2026-08-15 12:00:00 UTC",
        "duration_ms": "50.00",
        "interval_seconds": 60,
    }
    base.update(kwargs)
    return _ENV.get_template(name).render(**base)


def test_monitor_error_template_renders_detail():
    out = _render("telegram_monitor_error.j2")
    assert "Monitoring failure" in out
    assert "rockstar" in out
    assert "HTTP 403" in out


def test_monitor_error_template_is_visually_distinct_from_service_alert():
    failure = _render("telegram_monitor_error.j2")
    alert = _render("telegram_alert.j2", reason_items=["Store: down"])
    assert "🛑" in failure and "🛑" not in alert
    assert "🚨" in alert and "🚨" not in failure


def test_monitor_error_template_explains_it_is_about_monitoring():
    out = _render("telegram_monitor_error.j2")
    assert "cannot reach this provider" in out
    assert "about the monitoring" in out


def test_monitor_recovered_template_renders():
    out = _render("telegram_monitor_recovered.j2", level="INFO", status="OK")
    assert "Monitoring restored" in out
    assert "🔄" in out
    assert "rockstar" in out


def test_monitor_recovered_is_distinct_from_service_resolved():
    monitor = _render("telegram_monitor_recovered.j2", level="INFO", status="OK")
    service = _render("telegram_resolved.j2", level="INFO", status="OK", services=[])
    assert "Monitoring restored" in monitor
    assert "Monitoring restored" not in service
    assert "Resolved" in service


def test_monitor_templates_escape_hostile_reason():
    out = _render("telegram_monitor_error.j2", reason="<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
