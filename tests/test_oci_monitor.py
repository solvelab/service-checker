"""Tests for the OCI monitor and for per-component state-key distinctness.

Every incident used to fall back to the literal key ``oci:service`` because OCI items
carry no id/slug/name, so two simultaneous incidents shared one throttle window and the
second one was silently suppressed.
"""
from __future__ import annotations

import asyncio
import logging
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
from app.core.notifications import NotificationManager, _content_digest, _service_key
from app.core.types import MonitorResult, MonitorStatus
from app.modules.oci.monitor import (
    OciStatusMonitor,
    _incident_id,
    _parse_incidents,
    get_monitor,
)

FIXTURE = Path(__file__).parent / "fixtures" / "oci" / "incident_summary.rss"


def _feed() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _module_config(service_filter=None, rule_value="investigating,identified,monitoring"):
    return ModuleConfig(
        slug="oci",
        url="https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(monitor, body):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.text = body
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return asyncio.run(
        monitor.check(http_client=client, logger=logging.getLogger("test"))
    )


# ---------------------------------------------------------------------------
# Parser against the real feed
# ---------------------------------------------------------------------------

def test_parses_the_real_feed():
    incidents = _parse_incidents(_feed())
    assert len(incidents) == 25
    for incident in incidents:
        assert incident["id"]
        assert incident["name"]
        assert incident["title"]


def test_every_incident_in_the_real_feed_gets_a_distinct_id():
    incidents = _parse_incidents(_feed())
    ids = [i["id"] for i in incidents]
    assert len(set(ids)) == len(ids)


def test_ids_are_stable_across_parses():
    """An unstable id would re-alert every single cycle — the inverse failure."""
    first = [i["id"] for i in _parse_incidents(_feed())]
    second = [i["id"] for i in _parse_incidents(_feed())]
    assert first == second


def test_id_prefers_the_title_reference():
    assert _incident_id("210f910e", "Networking | US East | 210f910e", "http://x") == "210f910e"


def test_id_is_lowercased_and_slugified():
    assert _incident_id("CN-6256486", "t", "l") == "cn-6256486"


def test_id_falls_back_to_link_then_title():
    assert _incident_id("", "Some Title", "https://x/ocid1.abc") == "https-x-ocid1-abc"
    assert _incident_id("", "Some Title", "") == "some-title"


def test_id_never_empty():
    assert _incident_id("", "", "") == "unknown-incident"


def test_name_is_the_service_for_readable_cards():
    incidents = _parse_incidents(_feed())
    assert incidents[0]["name"] == incidents[0]["service"]


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------

def test_check_ok_when_no_incident_matches_the_rule():
    monitor = OciStatusMonitor()
    monitor.configure(_module_config(rule_value="investigating"))
    result = _run(monitor, _feed())
    # The captured feed holds resolved incidents only.
    assert result.status == MonitorStatus.OK


def test_check_alerts_on_matching_status():
    monitor = OciStatusMonitor()
    monitor.configure(_module_config(rule_value="resolved"))
    result = _run(monitor, _feed())
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 25


def test_check_request_failure_returns_error():
    monitor = OciStatusMonitor()
    monitor.configure(_module_config())
    client = MagicMock()
    client.get = AsyncMock(side_effect=OSError("boom"))
    result = asyncio.run(
        monitor.check(http_client=client, logger=logging.getLogger("test"))
    )
    assert result.status == MonitorStatus.ERROR
    assert "boom" in (result.reason or "")


def test_check_malformed_xml_returns_error():
    monitor = OciStatusMonitor()
    monitor.configure(_module_config())
    result = _run(monitor, "<rss><item></rss>")
    assert result.status == MonitorStatus.ERROR
    assert "parse" in (result.reason or "").lower()


def test_get_monitor_slug():
    assert get_monitor("oci").id == "oci"


# ---------------------------------------------------------------------------
# _service_key distinctness
# ---------------------------------------------------------------------------

def test_service_key_uses_the_incident_id():
    incidents = _parse_incidents(_feed())
    keys = [_service_key("oci", i) for i in incidents]
    assert len(set(keys)) == len(keys)
    assert keys[0] == f"oci:{incidents[0]['id']}"


def test_service_key_no_longer_collapses_items_without_identifiers():
    """The old literal 'service' fallback made every such item share one key."""
    aws_like = [
        {"service": "EC2", "region": "us-east-1", "typeCode": "operational_issue"},
        {"service": "S3", "region": "sa-east-1", "typeCode": "operational_issue"},
    ]
    keys = [_service_key("aws", i) for i in aws_like]
    assert len(set(keys)) == 2
    assert "aws:service" not in keys


def test_content_digest_is_stable_and_order_insensitive():
    a = {"service": "EC2", "region": "us-east-1"}
    b = {"region": "us-east-1", "service": "EC2"}
    assert _content_digest(a) == _content_digest(b)


def test_content_digest_differs_for_different_content():
    a = {"service": "EC2", "region": "us-east-1"}
    b = {"service": "EC2", "region": "sa-east-1"}
    assert _content_digest(a) != _content_digest(b)


def test_content_digest_survives_unserialisable_values():
    assert _content_digest({"when": object()}).startswith("sha-")


def test_service_key_fallback_is_logged():
    logger = MagicMock(spec=logging.Logger)
    _service_key("aws", {"service": "EC2", "region": "us-east-1"}, logger)
    logger.warning.assert_called_once()
    extra = logger.warning.call_args[1]["extra"]
    assert extra["event"] == "service_key_fallback"
    assert extra["module_id"] == "aws"


def test_service_key_with_identifier_logs_nothing():
    logger = MagicMock(spec=logging.Logger)
    _service_key("oci", {"id": "210f910e"}, logger)
    logger.warning.assert_not_called()


@pytest.mark.parametrize(
    "item,expected",
    [
        ({"id": "a", "slug": "b", "name": "c"}, "m:a"),
        ({"slug": "b", "name": "c"}, "m:b"),
        ({"name": "c"}, "m:c"),
    ],
)
def test_service_key_precedence_unchanged(item, expected):
    assert _service_key("m", item) == expected


# ---------------------------------------------------------------------------
# End-to-end through the NotificationManager
# ---------------------------------------------------------------------------

def _spy_manager():
    config = NotificationConfig(
        telegram=TelegramConfig(True, "tok", ["1"], "https://api.telegram.org", "%Y", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10,
        error_threshold=3,
    )
    manager = NotificationManager(config)
    stub = MagicMock()
    for method in ("send_alert", "send_recovery", "send_monitor_error", "send_monitor_recovered"):
        setattr(stub, method, AsyncMock())
    manager.telegram_notifier = stub
    return manager


async def _feed_result(manager, status, payload, when):
    from datetime import datetime, timedelta, timezone

    await manager.handle_result(
        module_id="oci",
        result=MonitorResult(status, "msg", "reason", 10.0, payload),
        module_config=_module_config(),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=when),
        http_client=AsyncMock(),
        logger=MagicMock(spec=logging.Logger),
    )


@pytest.mark.asyncio
async def test_two_simultaneous_incidents_produce_two_alerts():
    manager = _spy_manager()
    items = [
        {"id": "aaa111", "name": "Compute", "region": "Brazil East", "status": "Investigating"},
        {"id": "bbb222", "name": "Storage", "region": "Brazil Southeast", "status": "Identified"},
    ]
    await _feed_result(manager, MonitorStatus.ALERT, items, 0)
    assert manager.telegram_notifier.send_alert.call_count == 2


@pytest.mark.asyncio
async def test_same_incident_twice_is_throttled_not_duplicated():
    manager = _spy_manager()
    items = [{"id": "aaa111", "name": "Compute", "status": "Investigating"}]
    await _feed_result(manager, MonitorStatus.ALERT, items, 0)
    await _feed_result(manager, MonitorStatus.ALERT, items, 1)
    assert manager.telegram_notifier.send_alert.call_count == 1


@pytest.mark.asyncio
async def test_recovering_one_incident_does_not_clear_the_other():
    manager = _spy_manager()
    both = [
        {"id": "aaa111", "name": "Compute", "status": "Investigating"},
        {"id": "bbb222", "name": "Storage", "status": "Identified"},
    ]
    await _feed_result(manager, MonitorStatus.ALERT, both, 0)
    assert manager.telegram_notifier.send_alert.call_count == 2

    # Only the first one clears. The module reports ALERT while Storage is still
    # degraded, and an ALERT payload carries only the incidents that still match the
    # rule (`_evaluate_status_rule` returns `matches`) — so a cleared incident leaves
    # the payload rather than appearing in it as resolved.
    await _feed_result(
        manager, MonitorStatus.ALERT, [{"id": "bbb222", "name": "Storage", "status": "Identified"}], 1
    )
    assert manager.telegram_notifier.send_recovery.call_count == 1

    # The second is still pending, so it must stay throttled rather than re-alert.
    await _feed_result(
        manager, MonitorStatus.ALERT, [{"id": "bbb222", "name": "Storage", "status": "Identified"}], 2
    )
    assert manager.telegram_notifier.send_alert.call_count == 2


@pytest.mark.asyncio
async def test_items_without_identifiers_alert_independently():
    """Regression for the original bug, using the shape a module without ids emits."""
    manager = _spy_manager()
    items = [
        {"service": "EC2", "region": "us-east-1", "typeCode": "operational_issue"},
        {"service": "S3", "region": "sa-east-1", "typeCode": "operational_issue"},
    ]
    await _feed_result(manager, MonitorStatus.ALERT, items, 0)
    assert manager.telegram_notifier.send_alert.call_count == 2
