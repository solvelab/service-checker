"""Tests for recovery notification component identification."""

import json
import logging
from datetime import datetime, timezone
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
)
from app.core.logging import JsonFormatter
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus
from app.notifications.telegram.notifier import _serialize_services


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_notification_config(*, telegram_enabled=False, webhook_enabled=False):
    return NotificationConfig(
        telegram=TelegramConfig(
            enabled=telegram_enabled,
            bot_token="tok",
            chat_ids=["123"],
            api_url="https://api.telegram.org",
            timestamp_format="%Y-%m-%d %H:%M:%S %Z",
            timestamp_zone="UTC",
        ),
        webhook=WebhookConfig(
            enabled=webhook_enabled,
            url="https://hook.example.com",
            token="secret",
            header_name="Authorization",
        ),
        repeat_minutes=10,
    )


def _make_module_config(slug="openai"):
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


def _make_event_time():
    return datetime(2026, 2, 7, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test: Service recovery includes component details (from_status / to_status)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_recovery_includes_component_details():
    """Recovery MonitorResult payload items should carry from_status and to_status."""
    config = _make_notification_config(telegram_enabled=True)
    manager = NotificationManager(config)
    manager.telegram_notifier = MagicMock()
    manager.telegram_notifier.send_alert = AsyncMock()
    manager.telegram_notifier.send_recovery = AsyncMock()

    module_config = _make_module_config()
    http_client = AsyncMock()
    logger = MagicMock(spec=logging.Logger)
    logger.getChild = MagicMock(return_value=logger)
    event_time = _make_event_time()

    # Phase 1: trigger ALERT for a specific component
    alert_item = {"id": "api", "name": "API", "status": "degraded_performance", "slug": "api"}
    alert_result = MonitorResult(
        status=MonitorStatus.ALERT,
        message="service degraded",
        reason="API: degraded_performance",
        duration_ms=100.0,
        payload=[alert_item],
    )
    await manager.handle_result(
        module_id="openai",
        result=alert_result,
        module_config=module_config,
        level_name="WARNING",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Phase 2: trigger OK for the same component
    ok_item = {"id": "api", "name": "API", "status": "operational", "slug": "api"}
    ok_result = MonitorResult(
        status=MonitorStatus.OK,
        message="all operational",
        reason=None,
        duration_ms=80.0,
        payload=[ok_item],
    )
    await manager.handle_result(
        module_id="openai",
        result=ok_result,
        module_config=module_config,
        level_name="INFO",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Verify send_recovery was called
    manager.telegram_notifier.send_recovery.assert_called_once()
    call_kwargs = manager.telegram_notifier.send_recovery.call_args[1]
    recovery_result = call_kwargs["result"]

    # The payload should contain the enriched item
    assert len(recovery_result.payload) == 1
    item = recovery_result.payload[0]
    assert item["from_status"] == "degraded_performance"
    assert item["to_status"] == "operational"
    assert item["name"] == "API"
    assert item["id"] == "api"


# ---------------------------------------------------------------------------
# Test: Module-level recovery has no component section
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_module_level_recovery_has_no_component_section():
    """Module-level recoveries (no service items) should not carry service payload."""
    config = _make_notification_config(telegram_enabled=True)
    manager = NotificationManager(config)
    manager.telegram_notifier = MagicMock()
    manager.telegram_notifier.send_alert = AsyncMock()
    manager.telegram_notifier.send_recovery = AsyncMock()

    module_config = _make_module_config("mymodule")
    http_client = AsyncMock()
    logger = MagicMock(spec=logging.Logger)
    event_time = _make_event_time()

    # Phase 1: ALERT with no payload (module-level)
    alert_result = MonitorResult(
        status=MonitorStatus.ALERT,
        message="module down",
        reason="connection timeout",
        duration_ms=500.0,
        payload=None,
    )
    await manager.handle_result(
        module_id="mymodule",
        result=alert_result,
        module_config=module_config,
        level_name="WARNING",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Phase 2: OK with no payload
    ok_result = MonitorResult(
        status=MonitorStatus.OK,
        message="module ok",
        reason=None,
        duration_ms=50.0,
        payload=None,
    )
    await manager.handle_result(
        module_id="mymodule",
        result=ok_result,
        module_config=module_config,
        level_name="INFO",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    manager.telegram_notifier.send_recovery.assert_called_once()
    call_kwargs = manager.telegram_notifier.send_recovery.call_args[1]
    recovery_result = call_kwargs["result"]
    # Module-level: payload should be None (no services)
    assert recovery_result.payload is None


# ---------------------------------------------------------------------------
# Test: Multi-component partial recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_component_partial_recovery():
    """When multiple components go ALERT then recover, each gets its own notification."""
    config = _make_notification_config(telegram_enabled=True)
    manager = NotificationManager(config)
    manager.telegram_notifier = MagicMock()
    manager.telegram_notifier.send_alert = AsyncMock()
    manager.telegram_notifier.send_recovery = AsyncMock()

    module_config = _make_module_config()
    http_client = AsyncMock()
    logger = MagicMock(spec=logging.Logger)
    event_time = _make_event_time()

    # Phase 1: Both components ALERT
    items_alert = [
        {"id": "api", "name": "API", "status": "major_outage", "slug": "api"},
        {"id": "chat", "name": "Chat", "status": "partial_outage", "slug": "chat"},
    ]
    alert_result = MonitorResult(
        status=MonitorStatus.ALERT,
        message="service degraded",
        reason="API: major_outage, Chat: partial_outage",
        duration_ms=100.0,
        payload=items_alert,
    )
    await manager.handle_result(
        module_id="openai",
        result=alert_result,
        module_config=module_config,
        level_name="WARNING",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Phase 2: Both components recover
    items_ok = [
        {"id": "api", "name": "API", "status": "operational", "slug": "api"},
        {"id": "chat", "name": "Chat", "status": "operational", "slug": "chat"},
    ]
    ok_result = MonitorResult(
        status=MonitorStatus.OK,
        message="all operational",
        reason=None,
        duration_ms=80.0,
        payload=items_ok,
    )
    await manager.handle_result(
        module_id="openai",
        result=ok_result,
        module_config=module_config,
        level_name="INFO",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Should have two recovery calls (one per component)
    assert manager.telegram_notifier.send_recovery.call_count == 2

    # Collect the recovered component names
    recovered_names = set()
    for call in manager.telegram_notifier.send_recovery.call_args_list:
        result = call[1]["result"]
        assert len(result.payload) == 1
        recovered_names.add(result.payload[0]["name"])
        # Each should have from_status and to_status
        assert result.payload[0]["from_status"]
        assert result.payload[0]["to_status"] == "operational"

    assert recovered_names == {"API", "Chat"}


# ---------------------------------------------------------------------------
# Test: _serialize_services includes new fields
# ---------------------------------------------------------------------------

def test_serialize_services_includes_new_fields():
    """_serialize_services should pass through id, slug, status, from_status, to_status."""
    payload = [
        {
            "id": "comp-1",
            "name": "API",
            "slug": "api",
            "status": "operational",
            "status_text": "",
            "severity": "minor",
            "from_status": "degraded_performance",
            "to_status": "operational",
        }
    ]
    result = _serialize_services(payload)
    assert len(result) == 1
    svc = result[0]
    assert svc["id"] == "comp-1"
    assert svc["slug"] == "api"
    assert svc["status"] == "operational"
    assert svc["from_status"] == "degraded_performance"
    assert svc["to_status"] == "operational"
    assert svc["name"] == "API"


def test_serialize_services_missing_new_fields():
    """Items without from_status/to_status should produce empty strings."""
    payload = [{"name": "SteamAPI", "id": "steam-api"}]
    result = _serialize_services(payload)
    assert len(result) == 1
    svc = result[0]
    assert svc["from_status"] == ""
    assert svc["to_status"] == ""
    assert svc["id"] == "steam-api"


# ---------------------------------------------------------------------------
# Test: Resolved template renders component details
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "app" / "notifications" / "telegram" / "templates"

_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("j2", "html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def test_resolved_template_renders_component():
    """Resolved template should show component name, id, and status transition."""
    template = _TEMPLATE_ENV.get_template("telegram_resolved.j2")
    rendered = template.render(
        module_id="openai",
        level="INFO",
        status="OK",
        message="service restored",
        timestamp="2026-02-07 12:00:00 UTC",
        duration_ms="80.00",
        interval_seconds=60,
        services=[
            {
                "name": "API",
                "id": "api",
                "slug": "api",
                "status": "operational",
                "status_text": "",
                "severity": "UNKNOWN",
                "from_status": "degraded_performance",
                "to_status": "operational",
            }
        ],
    )
    assert "API" in rendered
    assert "api" in rendered
    assert "degraded_performance" in rendered
    assert "operational" in rendered
    assert "Recovered component:" in rendered


def test_resolved_template_renders_without_services():
    """Resolved template without services should not show Recovered component section."""
    template = _TEMPLATE_ENV.get_template("telegram_resolved.j2")
    rendered = template.render(
        module_id="mymodule",
        level="INFO",
        status="OK",
        message="service restored",
        timestamp="2026-02-07 12:00:00 UTC",
        duration_ms="50.00",
        interval_seconds=60,
        services=[],
    )
    assert "Recovered component" not in rendered
    assert "mymodule" in rendered


# ---------------------------------------------------------------------------
# Test: Recovery logging includes check_id and transition info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_logging_includes_check_id():
    """Logger.info call during recovery should include check_id, from_status, to_status."""
    config = _make_notification_config()  # no notifiers enabled
    manager = NotificationManager(config)

    module_config = _make_module_config()
    http_client = AsyncMock()
    logger = MagicMock(spec=logging.Logger)
    event_time = _make_event_time()

    # ALERT
    alert_item = {"id": "api", "name": "API", "status": "major_outage", "slug": "api"}
    alert_result = MonitorResult(
        status=MonitorStatus.ALERT,
        message="degraded",
        reason="API: major_outage",
        duration_ms=100.0,
        payload=[alert_item],
    )
    await manager.handle_result(
        module_id="openai",
        result=alert_result,
        module_config=module_config,
        level_name="WARNING",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    logger.info.reset_mock()

    # OK
    ok_item = {"id": "api", "name": "API", "status": "operational", "slug": "api"}
    ok_result = MonitorResult(
        status=MonitorStatus.OK,
        message="ok",
        reason=None,
        duration_ms=50.0,
        payload=[ok_item],
    )
    await manager.handle_result(
        module_id="openai",
        result=ok_result,
        module_config=module_config,
        level_name="INFO",
        event_name="monitor_check",
        event_time=event_time,
        http_client=http_client,
        logger=logger,
    )

    # Find the recovery log call
    recovery_calls = [
        c for c in logger.info.call_args_list
        if c[0][0] == "recovery notification emitted"
    ]
    assert len(recovery_calls) == 1
    extra = recovery_calls[0][1]["extra"]
    assert extra["check_id"] == "openai:api"
    assert extra["from_status"] == "major_outage"
    assert extra["to_status"] == "operational"
    assert extra["event"] == "service_recovery"


# ---------------------------------------------------------------------------
# Test: JsonFormatter includes new keys
# ---------------------------------------------------------------------------

def test_json_formatter_includes_new_keys():
    """JsonFormatter should emit check_id, from_status, to_status when present."""
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="recovery notification emitted",
        args=(),
        exc_info=None,
    )
    record.event = "service_recovery"
    record.module_id = "openai"
    record.check_id = "openai:api"
    record.from_status = "major_outage"
    record.to_status = "operational"

    output = formatter.format(record)
    data = json.loads(output)

    assert data["check_id"] == "openai:api"
    assert data["from_status"] == "major_outage"
    assert data["to_status"] == "operational"
    assert data["event"] == "service_recovery"
