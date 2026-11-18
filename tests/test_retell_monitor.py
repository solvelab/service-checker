"""Tests for the Retell AI status monitor (#116)."""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.retell.monitor import (
    RetellStatusMonitor,
    get_monitor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(
    slug: str = "retell",
    url: str = "https://status.retellai.com/api/v2/summary.json",
    rule_kind: str = "status",
    rule_value: str = "degraded_performance,partial_outage,major_outage",
    service_filter: list[str] | None = None,
) -> ModuleConfig:
    return ModuleConfig(
        slug=slug,
        url=url,
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


FIXTURE = Path(__file__).parent / "fixtures" / "retell" / "summary.json"


def _real_summary() -> dict:
    """The live payload of 2026-11-18: 15 components, all operational."""
    return copy.deepcopy(json.loads(FIXTURE.read_text()))


def _summary_data(
    indicator: str = "none",
    description: str = "All Systems Operational",
    components: list[dict] | None = None,
) -> dict:
    if components is None:
        components = [
            {"id": "abc1", "name": "API", "status": "operational"},
            {"id": "abc2", "name": "Phone Call", "status": "operational"},
            {"id": "abc3", "name": "Web Call", "status": "operational"},
        ]
    return {
        "page": {"id": "8x029ww7t6mg", "name": "Retell"},
        "status": {"indicator": indicator, "description": description},
        "components": components,
    }


def _incidents_data(incidents: list[dict] | None = None) -> dict:
    return {"page": {"id": "8x029ww7t6mg"}, "incidents": incidents or []}


def _maintenances_data(maintenances: list[dict] | None = None) -> dict:
    return {"page": {"id": "8x029ww7t6mg"}, "scheduled_maintenances": maintenances or []}


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_get_monitor_factory():
    monitor = get_monitor("retell")
    assert isinstance(monitor, RetellStatusMonitor)
    assert monitor.id == "retell"


def test_get_monitor_custom_slug():
    monitor = get_monitor("retell-custom")
    assert monitor.id == "retell-custom"


# ---------------------------------------------------------------------------
# Healthy / degraded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_healthy():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK
    assert result.message == "retell status healthy"
    assert result.duration_ms is not None
    assert len(result.payload) == 3


@pytest.mark.asyncio
async def test_check_degraded_components():
    components = [
        {"id": "abc1", "name": "API", "status": "operational"},
        {"id": "abc3", "name": "Web Call", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert result.message == "retell status degraded"
    assert "Web Call: partial_outage" in result.reason
    assert len(result.payload) == 1
    assert result.payload[0]["name"] == "Web Call"


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_incidents_enrichment():
    components = [{"id": "abc3", "name": "Web Call", "status": "major_outage"}]
    data = _summary_data(indicator="major", components=components)

    incidents = [
        {
            "name": "Pipelines build failures",
            "status": "investigating",
            "created_at": "2026-04-28T12:00:00Z",
            "updated_at": "2026-04-28T12:30:00Z",
        }
    ]

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data(incidents)),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Web Call: major_outage" in result.reason
    assert "Incident: Pipelines build failures" in result.reason
    assert "investigating" in result.reason


@pytest.mark.asyncio
async def test_check_with_maintenance_enrichment():
    components = [
        {"id": "abc3", "name": "Web Call", "status": "under_maintenance"},
    ]
    data = _summary_data(indicator="maintenance", components=components)

    maintenances = [
        {
            "name": "Scheduled maintenance for Pipelines",
            "status": "in_progress",
            "scheduled_for": "2026-04-28T13:00:00Z",
            "updated_at": "2026-04-28T13:05:00Z",
        }
    ]

    monitor = RetellStatusMonitor()
    monitor.configure(
        _make_config(
            rule_value="degraded_performance,partial_outage,major_outage,under_maintenance"
        )
    )

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data(maintenances)),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Maintenance: Scheduled maintenance for Pipelines" in result.reason
    assert "in_progress" in result.reason


@pytest.mark.asyncio
async def test_enrichment_failure_non_fatal():
    components = [{"id": "abc3", "name": "Web Call", "status": "partial_outage"}]
    data = _summary_data(indicator="major", components=components)

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        httpx.ConnectError("enrichment failed"),
        httpx.ConnectError("enrichment failed"),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Web Call: partial_outage" in result.reason
    assert "Incident" not in result.reason


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_api_failure():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.ConnectError("DNS resolution failed")
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert result.message == "retell status request failed"
    assert "DNS resolution failed" in result.reason
    assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_check_timeout():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.ReadTimeout("read timed out")
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "read timed out" in result.reason


# ---------------------------------------------------------------------------
# Service filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_service_filter():
    components = [
        {"id": "abc1", "name": "API", "status": "partial_outage"},
        {"id": "abc3", "name": "Web Call", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(service_filter=["web-call"]))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Web Call" in result.reason
    assert "API" not in result.reason
    assert len(result.payload) == 1


@pytest.mark.asyncio
async def test_check_service_filter_no_match():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(service_filter=["nonexistent"]))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "no target components matched filter" in result.reason


# ---------------------------------------------------------------------------
# Keyword / regex
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_keyword_rule_match():
    data = _summary_data(indicator="major", description="Partial System Outage")

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="Partial System"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "keyword" in result.reason


@pytest.mark.asyncio
async def test_check_keyword_rule_no_match():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="catastrophic"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_check_regex_rule_match():
    data = _summary_data(indicator="major")

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value=r"major"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_check_regex_rule_invalid():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value=r"[invalid"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in result.reason


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_empty_components():
    data = _summary_data(components=[])

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "no components" in result.reason


@pytest.mark.asyncio
async def test_check_unconfigured_raises():
    monitor = RetellStatusMonitor()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(RuntimeError, match="retell monitor not configured"):
        await monitor.check(http_client, logger)


@pytest.mark.asyncio
async def test_check_empty_rule_value_uses_defaults():
    components = [{"id": "1", "name": "API", "status": "degraded_performance"}]
    data = _summary_data(components=components)

    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_value=""))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "API: degraded_performance" in result.reason


# ---------------------------------------------------------------------------
# The real payload and the generic rule default (#116)
# ---------------------------------------------------------------------------

def _client(*payloads):
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [_mock_response(p) for p in payloads]
    return http_client


def _degrade(data: dict, name: str, status: str) -> dict:
    for comp in data["components"]:
        if comp["name"] == name:
            comp["status"] = status
            return data
    raise AssertionError(f"{name} is not in the fixture")


@pytest.mark.asyncio
async def test_the_real_payload_is_healthy():
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    result = await monitor.check(_client(_real_summary()), MagicMock(spec=logging.Logger))

    assert result.status == MonitorStatus.OK
    assert len(result.payload) == 15
    assert {"API", "Phone Call", "Web Call", "Batch Call", "End to End Calling"} <= {
        c["name"] for c in result.payload}


@pytest.mark.asyncio
async def test_a_degraded_child_alerts_together_with_its_group():
    data = _degrade(_real_summary(), "Phone Call", "partial_outage")
    data = _degrade(data, "End to End Calling", "partial_outage")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    result = await monitor.check(
        _client(data, _incidents_data(), _maintenances_data()),
        MagicMock(spec=logging.Logger),
    )

    assert result.status == MonitorStatus.ALERT
    assert sorted(result.reason_items) == [
        "End to End Calling: partial_outage", "Phone Call: partial_outage"]


@pytest.mark.parametrize("rule_value", ["major,minor", "major", "none,critical"])
@pytest.mark.asyncio
async def test_a_rule_value_naming_no_component_status_still_alerts(rule_value):
    # `major,minor` is the generic RULE_VALUE default: taken literally it matches no
    # component status, and a minimal configuration would never alert.
    data = _degrade(_real_summary(), "API", "major_outage")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_value=rule_value))

    result = await monitor.check(
        _client(data, _incidents_data(), _maintenances_data()),
        MagicMock(spec=logging.Logger),
    )

    assert result.status == MonitorStatus.ALERT
    assert result.reason == "API: major_outage"


@pytest.mark.asyncio
async def test_a_rule_value_with_a_component_status_is_taken_as_written():
    # One real status in the list: the operator chose, so only that one alerts.
    data = _degrade(_real_summary(), "API", "degraded_performance")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_value="major,major_outage"))

    result = await monitor.check(_client(data), MagicMock(spec=logging.Logger))

    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_maintenance_alerts_only_when_asked():
    data = _degrade(_real_summary(), "Dashboard", "under_maintenance")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(rule_value="major,minor"))
    assert (await monitor.check(_client(data), MagicMock(spec=logging.Logger))).status \
        == MonitorStatus.OK

    monitor.configure(_make_config(rule_value="under_maintenance"))
    result = await monitor.check(
        _client(data, _incidents_data(), _maintenances_data()),
        MagicMock(spec=logging.Logger),
    )
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_the_filter_takes_the_real_names():
    data = _degrade(_real_summary(), "Batch Call", "major_outage")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config(service_filter=["api", "phone-call"]))

    result = await monitor.check(_client(data), MagicMock(spec=logging.Logger))

    assert result.status == MonitorStatus.OK
    assert [c["name"] for c in result.payload] == ["API", "Phone Call"]


def test_the_default_url_is_the_retell_statuspage(monkeypatch):
    from app.core.config import _load_module_config, load_app_config

    for name in ("RETELL_URL", "RETELL_RULE_VALUE", "RETELL_SERVICE_FILTER"):
        monkeypatch.delenv(name, raising=False)
    defaults = load_app_config().defaults
    config = _load_module_config("retell", defaults)
    assert config.url == "https://status.retellai.com/api/v2/summary.json"
    assert config.rule.kind == "status"


# ---------------------------------------------------------------------------
# Found by the bug-hunter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("incidents, maintenances", [
    ([], _maintenances_data()),
    ({"incidents": None}, _maintenances_data()),
    ({"incidents": {"a": 1}}, _maintenances_data()),
    ({"incidents": ["boom"]}, _maintenances_data()),
    (_incidents_data(), {"scheduled_maintenances": [None]}),
])
@pytest.mark.asyncio
async def test_a_malformed_enrichment_body_still_alerts(incidents, maintenances):
    data = _degrade(_real_summary(), "API", "major_outage")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    result = await monitor.check(
        _client(data, incidents, maintenances), MagicMock(spec=logging.Logger))

    assert result.status == MonitorStatus.ALERT
    assert result.reason == "API: major_outage"


@pytest.mark.asyncio
async def test_an_incident_with_null_fields_renders_unknown():
    data = _degrade(_real_summary(), "API", "major_outage")
    monitor = RetellStatusMonitor()
    monitor.configure(_make_config())

    result = await monitor.check(
        _client(data, {"incidents": [{"name": None, "status": None}]}, _maintenances_data()),
        MagicMock(spec=logging.Logger),
    )

    assert "Incident: unknown (unknown, )" in result.reason_items
