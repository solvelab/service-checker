"""Tests for the GitHub Status monitor."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorResult, MonitorStatus
from app.modules.github.monitor import (
    GitHubStatusMonitor,
    _extract_components,
    _slugify,
    get_monitor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(
    slug: str = "github",
    url: str = "https://www.githubstatus.com/api/v2/summary.json",
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
            {"id": "br0l2tvcx85d", "name": "Git Operations", "status": "operational"},
            {"id": "hhtssxt0f5v2", "name": "API Requests", "status": "operational"},
            {"id": "8l4ygp009s5s", "name": "Actions", "status": "operational"},
        ]
    return {
        "page": {"id": "kctbh9vrtdwd", "name": "GitHub"},
        "status": {"indicator": indicator, "description": description},
        "components": components,
    }


def _incidents_data(incidents: list[dict] | None = None) -> dict:
    if incidents is None:
        incidents = []
    return {"page": {"id": "kctbh9vrtdwd"}, "incidents": incidents}


def _maintenances_data(maintenances: list[dict] | None = None) -> dict:
    if maintenances is None:
        maintenances = []
    return {"page": {"id": "kctbh9vrtdwd"}, "scheduled_maintenances": maintenances}


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Unit tests: _extract_components / _slugify
# ---------------------------------------------------------------------------

def test_slugify():
    assert _slugify("API Requests") == "api-requests"
    assert _slugify("Git Operations") == "git-operations"
    assert _slugify("  Hello World  ") == "hello-world"


def test_extract_components_basic():
    data = _summary_data()
    comps = _extract_components(data)
    assert len(comps) == 3
    assert comps[0]["name"] == "Git Operations"
    assert comps[0]["slug"] == "git-operations"
    assert comps[0]["status"] == "operational"


def test_extract_components_empty():
    comps = _extract_components({"components": []})
    assert comps == []


def test_extract_components_missing_key():
    comps = _extract_components({})
    assert comps == []


# ---------------------------------------------------------------------------
# Test: get_monitor factory
# ---------------------------------------------------------------------------

def test_get_monitor_factory():
    monitor = get_monitor("github")
    assert isinstance(monitor, GitHubStatusMonitor)
    assert monitor.id == "github"


def test_get_monitor_custom_slug():
    monitor = get_monitor("gh-custom")
    assert monitor.id == "gh-custom"


# ---------------------------------------------------------------------------
# Test: Healthy scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_healthy():
    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(_summary_data())
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK
    assert result.message == "github status healthy"
    assert result.duration_ms is not None
    assert result.payload is not None
    assert len(result.payload) == 3


# ---------------------------------------------------------------------------
# Test: Degraded components trigger ALERT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_degraded_components():
    components = [
        {"id": "br0l2tvcx85d", "name": "Git Operations", "status": "operational"},
        {"id": "8l4ygp009s5s", "name": "Actions", "status": "partial_outage"},
    ]
    data = _summary_data(
        indicator="major",
        description="Partial System Outage",
        components=components,
    )

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    # First call: summary; subsequent calls for enrichment return empty
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert result.message == "github status degraded"
    assert "Actions: partial_outage" in result.reason
    assert result.payload is not None
    assert len(result.payload) == 1
    assert result.payload[0]["name"] == "Actions"


# ---------------------------------------------------------------------------
# Test: Enrichment with active incidents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_incidents_enrichment():
    components = [
        {"id": "8l4ygp009s5s", "name": "Actions", "status": "major_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    incidents = [
        {
            "name": "Degraded performance for Actions",
            "status": "investigating",
            "created_at": "2026-02-09T19:00:00Z",
            "updated_at": "2026-02-09T19:30:00Z",
        }
    ]

    monitor = GitHubStatusMonitor()
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
    assert "Actions: major_outage" in result.reason
    assert "Incident: Degraded performance for Actions" in result.reason
    assert "investigating" in result.reason


# ---------------------------------------------------------------------------
# Test: Enrichment with active maintenances
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_maintenance_enrichment():
    components = [
        {"id": "8l4ygp009s5s", "name": "Actions", "status": "under_maintenance"},
    ]
    data = _summary_data(indicator="maintenance", components=components)

    maintenances = [
        {
            "name": "Scheduled maintenance for Actions",
            "status": "in_progress",
            "scheduled_for": "2026-02-09T20:00:00Z",
            "updated_at": "2026-02-09T20:05:00Z",
        }
    ]

    monitor = GitHubStatusMonitor()
    config = _make_config(
        rule_value="degraded_performance,partial_outage,major_outage,under_maintenance",
    )
    monitor.configure(config)

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data(maintenances)),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "Maintenance: Scheduled maintenance for Actions" in result.reason
    assert "in_progress" in result.reason


# ---------------------------------------------------------------------------
# Test: API failure returns ERROR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_api_failure():
    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.ConnectError("DNS resolution failed")
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert result.message == "github status request failed"
    assert "DNS resolution failed" in result.reason
    assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_check_timeout():
    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.ReadTimeout("read timed out")
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "read timed out" in result.reason


# ---------------------------------------------------------------------------
# Test: Enrichment failure is non-fatal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrichment_failure_non_fatal():
    components = [
        {"id": "8l4ygp009s5s", "name": "Actions", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        httpx.ConnectError("enrichment failed"),
        httpx.ConnectError("enrichment failed"),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    # Should still return ALERT with base reason, no enrichment
    assert result.status == MonitorStatus.ALERT
    assert "Actions: partial_outage" in result.reason
    assert "Incident" not in result.reason


# ---------------------------------------------------------------------------
# Test: Service filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_with_service_filter():
    components = [
        {"id": "br0l2tvcx85d", "name": "Git Operations", "status": "partial_outage"},
        {"id": "8l4ygp009s5s", "name": "Actions", "status": "partial_outage"},
    ]
    data = _summary_data(indicator="major", components=components)

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(service_filter=["actions"]))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = [
        _mock_response(data),
        _mock_response(_incidents_data()),
        _mock_response(_maintenances_data()),
    ]
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    # Only Actions should be in the reason, not Git Operations
    assert "Actions" in result.reason
    assert "Git Operations" not in result.reason
    assert len(result.payload) == 1


@pytest.mark.asyncio
async def test_check_service_filter_no_match():
    data = _summary_data()

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(service_filter=["nonexistent"]))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "no target components matched filter" in result.reason


# ---------------------------------------------------------------------------
# Test: Keyword rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_keyword_rule_match():
    data = _summary_data(indicator="major", description="Partial System Outage")

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="Partial System"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT
    assert "keyword" in result.reason


@pytest.mark.asyncio
async def test_check_keyword_rule_no_match():
    data = _summary_data()

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="catastrophic"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.OK


# ---------------------------------------------------------------------------
# Test: Regex rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_regex_rule_match():
    data = _summary_data(indicator="major")

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value=r"major"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_check_regex_rule_invalid():
    data = _summary_data()

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value=r"[invalid"))

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in result.reason


# ---------------------------------------------------------------------------
# Test: Empty components
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_empty_components():
    data = _summary_data(components=[])

    monitor = GitHubStatusMonitor()
    monitor.configure(_make_config())

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.return_value = _mock_response(data)
    logger = MagicMock(spec=logging.Logger)

    result = await monitor.check(http_client, logger)

    assert result.status == MonitorStatus.ERROR
    assert "no components" in result.reason


# ---------------------------------------------------------------------------
# Test: Unconfigured monitor raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_unconfigured_raises():
    monitor = GitHubStatusMonitor()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(RuntimeError, match="github monitor not configured"):
        await monitor.check(http_client, logger)


# ---------------------------------------------------------------------------
# Test: Default rule targets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_empty_rule_value_uses_defaults():
    """When rule_value is empty, should default to degraded/partial/major."""
    components = [
        {"id": "1", "name": "API", "status": "degraded_performance"},
    ]
    data = _summary_data(components=components)

    monitor = GitHubStatusMonitor()
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
