"""Tests for the AWS monitor.

The module used to read `region`, `endTime`, `typeCode` and `startTime` — none of
which exist in the public feed. `if not type_code: continue` therefore discarded
every event, and the module reported OK while AWS was publishing outages.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.notifications import _service_key
from app.core.types import MonitorStatus
from app.modules.aws.monitor import (
    AwsStatusMonitor,
    _matches_filter,
    _parse_event,
    get_monitor,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "aws"


def _events(name="current_events.json"):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _config(service_filter=None, rule_value="operational_issue"):
    return ModuleConfig(
        slug="aws",
        url="https://health.aws.amazon.com/public/currentevents",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(monitor, data):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=data)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


def _check(data, **kwargs):
    monitor = AwsStatusMonitor()
    monitor.configure(_config(**kwargs))
    return _run(monitor, data)


# ---------------------------------------------------------------------------
# ARN parsing — where region and type code actually live
# ---------------------------------------------------------------------------

def test_parses_region_and_type_code_out_of_the_arn():
    event = _events()[0]
    parsed = _parse_event(event)
    assert parsed["region"] == "eu-central-1"
    assert parsed["type_code"] == "AWS_DIRECTCONNECT_OPERATIONAL_ISSUE"


def test_parse_keeps_the_human_readable_fields():
    parsed = _parse_event(_events()[0])
    assert parsed["name"] == "AWS Direct Connect"
    assert parsed["region_name"] == "Frankfurt"
    assert parsed["summary"] == "Increased Packet loss"


def test_parse_carries_status_as_metadata_only():
    """Severity semantics are undocumented, so status must not gate alerting."""
    parsed = _parse_event(_events()[0])
    assert parsed["status"] == "1"


def test_region_falls_back_to_the_service_code_suffix():
    parsed = _parse_event({"arn": "not-an-arn", "service": "ec2-sa-east-1"})
    assert parsed["region"] == "sa-east-1"


def test_missing_arn_and_service_yields_no_region():
    assert _parse_event({})["region"] == ""


def test_unparseable_event_still_produces_a_dict():
    parsed = _parse_event({})
    assert parsed["name"] == "unknown"
    assert parsed["status"] == "unknown"


# ---------------------------------------------------------------------------
# Detection — the bug itself
# ---------------------------------------------------------------------------

def test_three_live_events_produce_three_alert_items():
    result = _check(_events())
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 3


def test_reason_names_region_and_service():
    result = _check(_events())
    assert "Frankfurt" in result.reason
    assert "AWS Direct Connect" in result.reason
    assert "Increased Packet loss" in result.reason


def test_empty_feed_is_ok():
    result = _check(_events("no_events.json"))
    assert result.status == MonitorStatus.OK
    assert result.payload == []


def test_non_list_payload_is_an_error():
    result = _check({"unexpected": "shape"})
    assert result.status == MonitorStatus.ERROR
    assert "unexpected" in (result.reason or "")


def test_non_dict_entries_are_skipped():
    result = _check(["garbage", 42, None] + _events())
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 3


# ---------------------------------------------------------------------------
# Stable identity
# ---------------------------------------------------------------------------

def test_every_event_gets_a_distinct_id():
    ids = [_parse_event(e)["id"] for e in _events()]
    assert len(set(ids)) == 3


def test_ids_are_stable_across_cycles():
    first = [_parse_event(e)["id"] for e in _events()]
    second = [_parse_event(e)["id"] for e in _events()]
    assert first == second


def test_id_comes_from_the_unique_arn_segment():
    parsed = _parse_event(_events()[0])
    assert parsed["id"].startswith("aws-directconnect-operational-issue-")


def test_two_events_occupy_two_state_keys():
    keys = [_service_key("aws", _parse_event(e)) for e in _events()]
    assert len(set(keys)) == 3
    assert "aws:service" not in keys


# ---------------------------------------------------------------------------
# Region filtering — never worked before
# ---------------------------------------------------------------------------

def test_filter_by_region_code():
    result = _check(_events(), service_filter=["eu-central-1"])
    assert result.status == MonitorStatus.ALERT
    assert [i["region"] for i in result.payload] == ["eu-central-1"]


def test_filter_by_human_region_name():
    result = _check(_events(), service_filter=["Frankfurt"])
    assert result.status == MonitorStatus.ALERT
    assert [i["region_name"] for i in result.payload] == ["Frankfurt"]


def test_filter_is_case_insensitive():
    result = _check(_events(), service_filter=["EU-CENTRAL-1"])
    assert len(result.payload) == 1


def test_filter_accepts_several_regions():
    result = _check(_events(), service_filter=["me-central-1", "me-south-1"])
    assert len(result.payload) == 2


def test_filter_matching_nothing_is_ok_not_error():
    """No incident in my regions is good news, not a misconfiguration."""
    result = _check(_events(), service_filter=["sa-east-1", "us-east-1"])
    assert result.status == MonitorStatus.OK
    assert result.payload == []


def test_no_filter_returns_every_event():
    assert len(_check(_events(), service_filter=[]).payload) == 3


def test_matches_filter_ignores_empty_candidates():
    assert _matches_filter({"region": "", "region_name": "", "service": "", "name": ""}, {""}) is False


# ---------------------------------------------------------------------------
# Rule value
# ---------------------------------------------------------------------------

def test_default_rule_value_still_matches_the_arn_type_code():
    """AWS_RULE_VALUE=operational_issue is the shipped default; it must keep working."""
    result = _check(_events(), rule_value="operational_issue")
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 3


def test_rule_value_that_matches_nothing_yields_ok():
    result = _check(_events(), rule_value="scheduled_maintenance")
    assert result.status == MonitorStatus.OK


def test_empty_rule_value_falls_back_to_operational_issue():
    result = _check(_events(), rule_value="")
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 3


def test_rule_value_narrowed_to_one_service_type():
    result = _check(_events(), rule_value="directconnect")
    assert len(result.payload) == 1


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_network_failure_returns_error():
    monitor = AwsStatusMonitor()
    monitor.configure(_config())
    client = MagicMock()
    client.get = AsyncMock(side_effect=OSError("boom"))
    result = asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))
    assert result.status == MonitorStatus.ERROR
    assert "boom" in (result.reason or "")
    assert result.duration_ms is not None


def test_unconfigured_monitor_raises():
    with pytest.raises(RuntimeError):
        _run(AwsStatusMonitor(), [])


def test_get_monitor_slug():
    assert get_monitor("aws").id == "aws"
