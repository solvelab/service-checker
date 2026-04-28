"""Tests for the Bitbucket Status monitor."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.bitbucket.monitor import (
    BitbucketStatusMonitor,
    _extract_components,
    _slugify,
    get_monitor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(
    slug: str = "bitbucket",
    url: str = "https://bitbucket.status.atlassian.com/api/v2/summary.json",
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


def _summary_data(
    indicator: str = "none",
    description: str = "All Systems Operational",
    components: list[dict] | None = None,
) -> dict:
    if components is None:
        components = [
            {"id": "abc1", "name": "Website", "status": "operational"},
            {"id": "abc2", "name": "Git via HTTPS", "status": "operational"},
            {"id": "abc3", "name": "Pipelines", "status": "operational"},
        ]
    return {
        "page": {"id": "btbckt", "name": "Bitbucket"},
        "status": {"indicator": indicator, "description": description},
        "components": components,
    }


def _incidents_data(incidents: list[dict] | None = None) -> dict:
    return {"page": {"id": "btbckt"}, "incidents": incidents or []}


def _maintenances_data(maintenances: list[dict] | None = None) -> dict:
    return {"page": {"id": "btbckt"}, "scheduled_maintenances": maintenances or []}


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_slugify():
    assert _slugify("Git via HTTPS") == "git-via-https"
    assert _slugify("Pull requests and code browsing") == "pull-requests-and-code-browsing"
    assert _slugify("  Hello World  ") == "hello-world"


def test_extract_components_basic():
    comps = _extract_components(_summary_data())
    assert len(comps) == 3
    assert comps[1]["name"] == "Git via HTTPS"
    assert comps[1]["slug"] == "git-via-https"
    assert comps[1]["status"] == "operational"


def test_extract_components_empty():
    assert _extract_components({"components": []}) == []
    assert _extract_components({}) == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_get_monitor_factory():
    monitor = get_monitor("bitbucket")
    assert isinstance(monitor, BitbucketStatusMonitor)
    assert monitor.id == "bitbucket"


def test_get_monitor_custom_slug():
    monitor = get_monitor("bb-custom")
    assert monitor.id == "bb-custom"


# ---------------------------------------------------------------------------
# Healthy / degraded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_healthy():
    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK
    assert result.message == "bitbucket status healthy"
    assert result.duration_ms is not None
    assert len(result.payload) == 3


@pytest.mark.asyncio
async def test_check_degraded_components():
    components = [
        {"id": "abc1", "name": "Website", "status": "operational"},
        {"id": "abc3", "name": "Pipelines", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = BitbucketStatusMonitor()
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
    assert result.message == "bitbucket status degraded"
    assert "Pipelines: partial_outage" in result.reason
    assert len(result.payload) == 1
    assert result.payload[0]["name"] == "Pipelines"


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_incidents_enrichment():
    components = [{"id": "abc3", "name": "Pipelines", "status": "major_outage"}]
    data = _summary_data(indicator="major", components=components)

    incidents = [
        {
            "name": "Pipelines build failures",
            "status": "investigating",
            "created_at": "2026-04-28T12:00:00Z",
            "updated_at": "2026-04-28T12:30:00Z",
        }
    ]

    monitor = BitbucketStatusMonitor()
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
    assert "Pipelines: major_outage" in result.reason
    assert "Incident: Pipelines build failures" in result.reason
    assert "investigating" in result.reason


@pytest.mark.asyncio
async def test_check_with_maintenance_enrichment():
    components = [
        {"id": "abc3", "name": "Pipelines", "status": "under_maintenance"},
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

    monitor = BitbucketStatusMonitor()
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
    components = [{"id": "abc3", "name": "Pipelines", "status": "partial_outage"}]
    data = _summary_data(indicator="major", components=components)

    monitor = BitbucketStatusMonitor()
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
    assert "Pipelines: partial_outage" in result.reason
    assert "Incident" not in result.reason


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_api_failure():
    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.ConnectError("DNS resolution failed")
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert result.message == "bitbucket status request failed"
    assert "DNS resolution failed" in result.reason
    assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_check_timeout():
    monitor = BitbucketStatusMonitor()
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
        {"id": "abc1", "name": "Website", "status": "partial_outage"},
        {"id": "abc3", "name": "Pipelines", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config(service_filter=["pipelines"]))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Pipelines" in result.reason
    assert "Website" not in result.reason
    assert len(result.payload) == 1


@pytest.mark.asyncio
async def test_check_service_filter_no_match():
    monitor = BitbucketStatusMonitor()
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

    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="Partial System"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "keyword" in result.reason


@pytest.mark.asyncio
async def test_check_keyword_rule_no_match():
    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="catastrophic"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_check_regex_rule_match():
    data = _summary_data(indicator="major")

    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value=r"major"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_check_regex_rule_invalid():
    monitor = BitbucketStatusMonitor()
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

    monitor = BitbucketStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "no components" in result.reason


@pytest.mark.asyncio
async def test_check_unconfigured_raises():
    monitor = BitbucketStatusMonitor()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(RuntimeError, match="bitbucket monitor not configured"):
        await monitor.check(http_client, logger)


@pytest.mark.asyncio
async def test_check_empty_rule_value_uses_defaults():
    components = [{"id": "1", "name": "API", "status": "degraded_performance"}]
    data = _summary_data(components=components)

    monitor = BitbucketStatusMonitor()
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
