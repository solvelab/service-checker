"""Tests for the GCP monitor.

The module's own logic is the distinction between an active incident and history:
the feed publishes both, and reading it wrong means either missing every outage or
alerting on incidents that closed months ago. Nothing verified that until now.
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
from app.modules.gcp.monitor import GcpStatusMonitor, _matches_location, get_monitor

FIXTURE = Path(__file__).parent / "fixtures" / "gcp" / "incidents.json"

_ACTIVE_STATUSES = "service_disruption,service_outage,service_information"


def _feed():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _active(impact="service_outage", locations=("us-central1",), incident_id="i1"):
    """One incident shaped like the feed's, but still open."""
    return {
        "id": incident_id,
        "status_impact": impact,
        "currently_affected_locations": [{"id": loc, "title": loc} for loc in locations],
        "most_recent_update": {"status": "AVAILABLE"},
    }


def _config(service_filter=None, rule_kind="status", rule_value=_ACTIVE_STATUSES):
    return ModuleConfig(
        slug="gcp",
        url="https://status.cloud.google.com/incidents.json",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(data, config=None, raises=None):
    monitor = GcpStatusMonitor()
    monitor.configure(config or _config())
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=data)
    client = MagicMock()
    client.get = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
    return asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


# ---------------------------------------------------------------------------
# Active vs history — the module's own logic
# ---------------------------------------------------------------------------

def test_the_real_feed_is_all_history_and_yields_ok():
    """Every incident in the captured feed has closed, so none should alert."""
    feed = _feed()
    assert all(i.get("end") for i in feed)
    assert _run(feed).status == MonitorStatus.OK


def test_an_open_incident_alerts():
    assert _run([_active()]).status == MonitorStatus.ALERT


def test_a_closed_incident_is_ignored_even_when_it_matches():
    closed = _active()
    closed["end"] = "2026-01-01T00:00:00Z"
    assert _run([closed]).status == MonitorStatus.OK


def test_an_incident_without_affected_locations_is_ignored():
    orphan = _active()
    orphan["currently_affected_locations"] = []
    assert _run([orphan]).status == MonitorStatus.OK


def test_the_fallback_location_field_is_honoured():
    incident = _active()
    incident.pop("currently_affected_locations")
    incident["affected_locations"] = [{"id": "us-central1"}]
    assert _run([incident]).status == MonitorStatus.ALERT


def test_a_closed_incident_from_the_real_feed_stays_ignored_when_reopened_shape():
    """Take a real incident, strip its end, and it becomes reportable."""
    incident = copy.deepcopy(_feed()[0])
    incident.pop("end", None)
    result = _run([incident], _config(rule_value=incident.get("status_impact") or "service_outage"))
    if incident.get("currently_affected_locations"):
        assert result.status == MonitorStatus.ALERT
    else:
        assert result.status == MonitorStatus.OK


# ---------------------------------------------------------------------------
# Status impact matching
# ---------------------------------------------------------------------------

def test_an_impact_outside_the_targets_is_ignored():
    assert _run([_active(impact="service_information")],
                _config(rule_value="service_outage")).status == MonitorStatus.OK


def test_an_empty_rule_value_falls_back_to_the_defaults():
    assert _run([_active(impact="service_outage")],
                _config(rule_value="")).status == MonitorStatus.ALERT


def test_an_incident_without_impact_is_still_evaluated():
    incident = _active()
    incident["status_impact"] = ""
    assert _run([incident]).status == MonitorStatus.ALERT


# ---------------------------------------------------------------------------
# Region filtering
# ---------------------------------------------------------------------------

def test_no_filter_evaluates_every_active_incident():
    assert len(_run([_active(locations=("europe-west1",))]).payload) == 1


def test_a_matching_region_alerts():
    result = _run([_active(locations=("us-central1",))], _config(service_filter=["us-central1"]))
    assert result.status == MonitorStatus.ALERT


def test_an_incident_outside_the_monitored_regions_is_ok_not_error():
    """No incident in my regions is good news, not a misconfiguration."""
    result = _run([_active(locations=("europe-west1",))], _config(service_filter=["us-central1"]))
    assert result.status == MonitorStatus.OK
    assert result.payload == []


def test_only_the_matching_regions_are_reported():
    result = _run(
        [_active(locations=("us-central1", "europe-west1"))],
        _config(service_filter=["us-central1"]),
    )
    assert result.payload[0]["regions"] == ["us-central1"]


def test_the_filter_matches_the_location_title_too():
    incident = _active()
    incident["currently_affected_locations"] = [{"id": "x", "title": "Iowa"}]
    assert _run([incident], _config(service_filter=["iowa"])).status == MonitorStatus.ALERT


def test_matches_location_without_targets_accepts_everything():
    assert _matches_location({"id": "anything"}, set()) is True


# ---------------------------------------------------------------------------
# Readable reporting — the repr leak fixed in #8
# ---------------------------------------------------------------------------

def test_regions_are_reported_without_python_repr():
    result = _run([_active(locations=("us-central1", "us-east1"))])
    assert "[" not in result.reason
    assert "'" not in result.reason


def test_multiple_regions_are_joined_readably():
    result = _run([_active(locations=("us-central1", "us-east1"))])
    assert "us-central1, us-east1: service_outage" in result.reason


def test_each_incident_becomes_one_reason_item():
    result = _run([_active(incident_id="i1"), _active(incident_id="i2", impact="service_disruption")])
    assert len(result.reason_items) == 2


# ---------------------------------------------------------------------------
# Rule strategies
# ---------------------------------------------------------------------------

def test_keyword_rule_matches():
    result = _run(_feed(), _config(rule_kind="keyword", rule_value="incident"))
    assert result.status == MonitorStatus.ALERT


def test_keyword_rule_without_match_is_ok():
    result = _run(_feed(), _config(rule_kind="keyword", rule_value="zzz-not-present"))
    assert result.status == MonitorStatus.OK


def test_regex_rule_matches():
    result = _run(_feed(), _config(rule_kind="regex", rule_value="incid[e]nt"))
    assert result.status == MonitorStatus.ALERT


def test_an_invalid_regex_is_an_error():
    result = _run(_feed(), _config(rule_kind="regex", rule_value="["))
    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in (result.reason or "")


def test_an_unsupported_rule_kind_is_an_error():
    result = _run(_feed(), _config(rule_kind="telepathy", rule_value="x"))
    assert result.status == MonitorStatus.ERROR
    assert "telepathy" in (result.reason or "")


def test_an_empty_rule_value_on_a_text_strategy_is_ok():
    assert _run(_feed(), _config(rule_kind="keyword", rule_value="")).status == MonitorStatus.OK


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_a_network_failure_is_an_error():
    result = _run(None, raises=OSError("connection refused"))
    assert result.status == MonitorStatus.ERROR
    assert "connection refused" in (result.reason or "")
    assert result.duration_ms is not None


def test_an_unexpected_payload_shape_is_an_error():
    result = _run({"incidents": []})
    assert result.status == MonitorStatus.ERROR
    assert "unexpected" in (result.reason or "")


def test_non_dict_entries_are_skipped():
    assert _run(["garbage", 42, None, _active()]).status == MonitorStatus.ALERT


def test_an_empty_feed_is_ok():
    result = _run([])
    assert result.status == MonitorStatus.OK
    assert result.payload == []


def test_an_unconfigured_monitor_raises():
    monitor = GcpStatusMonitor()
    client = MagicMock()
    client.get = AsyncMock()
    with pytest.raises(RuntimeError):
        asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


def test_get_monitor_returns_the_configured_slug():
    assert get_monitor("gcp").id == "gcp"
