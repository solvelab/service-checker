"""Tests for the Claude monitor.

Structurally a sibling of the GitHub and Bitbucket monitors, which carry 21 tests each.
This one inherited the code and not the coverage — which is exactly how the AWS module
went blind for months while the suite stayed green.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.claude.monitor import (
    ClaudeStatusMonitor,
    _extract_components,
    _slugify,
    get_monitor,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "summary.json"
_DEGRADED = "degraded_performance,partial_outage,major_outage"
_SLUG = "claude"
_MONITOR = ClaudeStatusMonitor


def _summary():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _degrade(summary, index=0, status="major_outage"):
    """The captured feed is healthy, so the incident case comes from an edited copy."""
    edited = copy.deepcopy(summary)
    edited["components"][index]["status"] = status
    return edited


def _config(service_filter=None, rule_kind="status", rule_value=_DEGRADED):
    return ModuleConfig(
        slug=_SLUG,
        url=f"https://status.{_SLUG}.com/api/v2/summary.json",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(data, config=None, raises=None):
    monitor = _MONITOR()
    monitor.configure(config or _config())
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=data)
    client = MagicMock()
    client.get = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
    return asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


# ---------------------------------------------------------------------------
# The captured feed
# ---------------------------------------------------------------------------

def test_the_real_feed_parses_into_components():
    components = _extract_components(_summary())
    assert components
    assert all(c["id"] and c["name"] and c["status"] and c["slug"] for c in components)


def test_the_captured_feed_is_healthy_and_yields_ok():
    result = _run(_summary())
    assert result.status == MonitorStatus.OK
    assert len(result.payload) == len(_summary()["components"])


def test_a_degraded_component_alerts():
    result = _run(_degrade(_summary()))
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 1


def test_the_reason_names_the_component_and_its_status():
    summary = _degrade(_summary())
    name = summary["components"][0]["name"]
    result = _run(summary)
    assert name in result.reason
    assert "major_outage" in result.reason


def test_two_degraded_components_render_two_reason_items():
    summary = _degrade(_degrade(_summary(), 0), 1, "partial_outage")
    result = _run(summary)
    assert len(result.reason_items) == 2


# ---------------------------------------------------------------------------
# Component identity — per-component alert lifecycle depends on it
# ---------------------------------------------------------------------------

def test_every_component_carries_id_name_and_slug():
    result = _run(_summary())
    assert all({"id", "name", "slug", "status"} <= set(item) for item in result.payload)


def test_component_ids_are_distinct():
    ids = [c["id"] for c in _extract_components(_summary())]
    assert len(set(ids)) == len(ids)


def test_a_component_without_an_id_falls_back_to_its_slug():
    component = _extract_components({"components": [{"name": "My Service", "status": "operational"}]})[0]
    assert component["id"] == "my-service"


def test_a_component_without_a_name_is_named_unknown():
    component = _extract_components({"components": [{"id": "x", "status": "operational"}]})[0]
    assert component["name"] == "unknown"


def test_slugify_normalises_punctuation():
    assert _slugify("API — v2 (beta)") == "api-v2-beta"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def test_no_filter_evaluates_every_component():
    assert len(_run(_summary()).payload) == len(_summary()["components"])


def test_the_filter_matches_by_id():
    summary = _degrade(_summary())
    target = summary["components"][0]["id"]
    result = _run(summary, _config(service_filter=[target]))
    assert result.status == MonitorStatus.ALERT
    assert [c["id"] for c in result.payload] == [target]


def test_the_filter_matches_by_name():
    summary = _degrade(_summary())
    target = summary["components"][0]["name"]
    result = _run(summary, _config(service_filter=[target]))
    assert result.status == MonitorStatus.ALERT


def test_a_filtered_out_component_does_not_alert():
    summary = _degrade(_summary(), 0)
    other = summary["components"][1]["id"]
    assert _run(summary, _config(service_filter=[other])).status == MonitorStatus.OK


def test_a_filter_matching_nothing_is_an_error_with_diagnostics():
    result = _run(_summary(), _config(service_filter=["no-such-component"]))
    assert result.status == MonitorStatus.ERROR
    assert "filter" in result.payload
    assert result.payload["components"]


# ---------------------------------------------------------------------------
# Rule strategies
# ---------------------------------------------------------------------------

def test_keyword_rule_matches():
    assert _run(_summary(), _config(rule_kind="keyword", rule_value="operational")).status == MonitorStatus.ALERT


def test_keyword_rule_without_match_is_ok():
    assert _run(_summary(), _config(rule_kind="keyword", rule_value="zzz")).status == MonitorStatus.OK


def test_regex_rule_matches():
    assert _run(_summary(), _config(rule_kind="regex", rule_value="oper[a]tional")).status == MonitorStatus.ALERT


def test_an_invalid_regex_is_an_error():
    result = _run(_summary(), _config(rule_kind="regex", rule_value="["))
    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in (result.reason or "")


def test_an_unsupported_rule_kind_is_an_error():
    result = _run(_summary(), _config(rule_kind="telepathy", rule_value="x"))
    assert result.status == MonitorStatus.ERROR
    assert "telepathy" in (result.reason or "")


def test_an_empty_rule_value_falls_back_to_the_default_targets():
    assert _run(_degrade(_summary()), _config(rule_value="")).status == MonitorStatus.ALERT


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_a_network_failure_is_an_error():
    result = _run(None, raises=OSError("connection refused"))
    assert result.status == MonitorStatus.ERROR
    assert "connection refused" in (result.reason or "")
    assert result.duration_ms is not None


def test_a_timeout_is_an_error():
    result = _run(None, raises=TimeoutError("timed out"))
    assert result.status == MonitorStatus.ERROR


def test_a_response_without_components_is_an_error_not_ok():
    """Absence of data is not evidence of health."""
    result = _run({"components": []})
    assert result.status == MonitorStatus.ERROR
    assert "no components" in (result.reason or "")


def test_a_response_missing_the_components_key_is_an_error():
    assert _run({"page": {}}).status == MonitorStatus.ERROR


def test_an_unconfigured_monitor_raises():
    monitor = _MONITOR()
    client = MagicMock()
    client.get = AsyncMock()
    with pytest.raises(RuntimeError):
        asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


def test_get_monitor_returns_the_configured_slug():
    assert get_monitor(_SLUG).id == _SLUG
