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


# ---------------------------------------------------------------------------
# Ended events still listed in the feed (issue #86)
#
# The feed keeps an event for a while after it ends, and only those carry
# `end_time`. Captured 2026-11-15: one "[RESOLVED]" event in us-east-1 with
# end_time set, and two active "Region Availability" events in me-central-1 and
# me-south-1 without it. Treating the first as active kept the alert open for more
# than nine hours and the all-clear never went out.
# ---------------------------------------------------------------------------

ENDED_FIXTURE = "current_events_with_ended.json"


def _ended_event():
    return next(e for e in _events(ENDED_FIXTURE) if e.get("end_time"))


def _evaluate(events, now=None, service_filter=None, logger=None):
    monitor = AwsStatusMonitor()
    monitor.configure(_config(service_filter=service_filter))
    return monitor._evaluate_rule(events, logger or logging.getLogger("test"), now=now)


def test_fixture_holds_one_ended_and_two_active_events():
    events = _events(ENDED_FIXTURE)
    assert len(events) == 3
    assert [bool(e.get("end_time")) for e in events].count(True) == 1
    assert _ended_event()["summary"].startswith("[RESOLVED]")


def test_parse_carries_end_time_as_ended_at():
    assert _parse_event(_ended_event())["ended_at"] == "1794754409"


def test_parse_without_end_time_yields_empty_ended_at():
    assert _parse_event(_events()[0])["ended_at"] == ""


def test_ended_event_in_the_watched_region_is_ok():
    result = _check(_events(ENDED_FIXTURE), service_filter=["us-east-1"])
    assert result.status == MonitorStatus.OK
    assert result.payload == []


def test_ended_event_is_left_out_while_active_ones_still_alert():
    result = _check(_events(ENDED_FIXTURE))
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 2
    assert "RESOLVED" not in result.reason
    assert {e["region"] for e in result.payload} == {"me-central-1", "me-south-1"}


def test_event_without_end_time_stays_active():
    event = dict(_ended_event())
    del event["end_time"]
    status, *_ = _evaluate([event], service_filter=["us-east-1"])
    assert status == MonitorStatus.ALERT


def test_resolved_summary_without_end_time_does_not_decide():
    """The summary is free text; only end_time ends an event."""
    event = dict(_ended_event())
    event.pop("end_time")
    assert event["summary"].startswith("[RESOLVED]")
    status, *_ = _evaluate([event])
    assert status == MonitorStatus.ALERT


def test_status_zero_without_end_time_does_not_decide():
    """Status is undocumented severity; the spec forbids deciding on it."""
    event = dict(_ended_event())
    event.pop("end_time")
    assert event["status"] == "0"
    status, *_ = _evaluate([event])
    assert status == MonitorStatus.ALERT


def test_end_time_in_the_future_keeps_the_event_active():
    event = dict(_ended_event())
    status, *_ = _evaluate([event], now=float(event["end_time"]) - 1)
    assert status == MonitorStatus.ALERT


def test_end_time_equal_to_now_ends_the_event():
    event = dict(_ended_event())
    status, *_ = _evaluate([event], now=float(event["end_time"]))
    assert status == MonitorStatus.OK


@pytest.mark.parametrize(
    "raw",
    [
        "soon", "1794754409Z", {"t": 1}, "2026-11-15T14:53:29Z",
        # parse as floats, but are not a valid epoch in seconds
        "nan", "inf", "-inf", "1e400",
        # non-positive: would otherwise end a live event (bug-hunter finding)
        "0", "-1",
        # milliseconds: would otherwise read as a far-future end, forever active
        "1794754409000",
    ],
)
def test_unreadable_end_time_keeps_the_event_active_and_logs(raw):
    event = dict(_ended_event(), end_time=raw)
    logger = MagicMock(spec=logging.Logger)
    status, *_ = _evaluate([event], logger=logger, now=1794754409 + 3600)
    assert status == MonitorStatus.ALERT
    logger.warning.assert_called_once()
    extra = logger.warning.call_args.kwargs["extra"]
    assert extra["event"] == "end_time_unreadable"
    assert extra["module_id"] == "aws"


def test_end_time_zero_as_string_and_as_int_agree():
    a = dict(_ended_event(), end_time="0")
    b = dict(_ended_event(), end_time=0)
    assert _evaluate([a])[0] == _evaluate([b])[0] == MonitorStatus.ALERT


def test_partial_end_recovers_only_the_ended_event():
    """Three events alert; one gains end_time; only that one recovers."""
    from datetime import datetime, timedelta, timezone

    from app.core.config import NotificationConfig, TelegramConfig, WebhookConfig
    from app.core.notifications import NotificationManager

    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
        )
    )
    spy = _Spy()
    manager.register("spy", spy)
    monitor = AwsStatusMonitor()
    monitor.configure(_config())

    events = _events(ENDED_FIXTURE)
    still_open = [e for e in events if not e.get("end_time")]
    reopened = {k: v for k, v in _ended_event().items() if k != "end_time"}
    t0 = datetime(2026, 11, 15, 12, 40, tzinfo=timezone.utc)

    async def cycle(feed, minute):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=feed)
        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        result = await monitor.check(http_client=client, logger=logging.getLogger("test"))
        await manager.handle_result(
            module_id="aws",
            result=result,
            module_config=_config(),
            level_name="WARNING",
            event_name="monitor_check",
            event_time=t0 + timedelta(minutes=minute),
            http_client=AsyncMock(),
            logger=MagicMock(spec=logging.Logger),
        )

    async def scenario():
        await cycle([reopened, *still_open], 0)
        await cycle(events, 1)
        assert (len(spy.alerts), len(spy.recoveries)) == (3, 1)
        await cycle(events, 15)
        assert (len(spy.alerts), len(spy.recoveries)) == (5, 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("raw", ["", None, 0])
def test_empty_end_time_means_active(raw):
    event = dict(_ended_event(), end_time=raw)
    status, *_ = _evaluate([event])
    assert status == MonitorStatus.ALERT


def test_end_time_as_a_number_is_accepted():
    event = dict(_ended_event(), end_time=1794754409)
    status, *_ = _evaluate([event], service_filter=["us-east-1"])
    assert status == MonitorStatus.OK


def test_event_reopened_by_dropping_end_time_alerts_again():
    ended = _ended_event()
    reopened = {k: v for k, v in ended.items() if k != "end_time"}
    assert _evaluate([ended], service_filter=["us-east-1"])[0] == MonitorStatus.OK
    assert _evaluate([reopened], service_filter=["us-east-1"])[0] == MonitorStatus.ALERT


# ---------------------------------------------------------------------------
# End to end: the real monitor feeding the real NotificationManager
# ---------------------------------------------------------------------------

class _Spy:
    def __init__(self):
        self.alerts = []
        self.recoveries = []

    async def send_alert(self, **kwargs):
        self.alerts.append(kwargs["result"].reason or "")
        return True

    async def send_recovery(self, **kwargs):
        self.recoveries.append(kwargs["result"].reason or "")
        return True

    async def send_monitor_error(self, **kwargs):
        return True

    async def send_monitor_recovered(self, **kwargs):
        return True


def test_end_time_appearing_emits_the_all_clear():
    from datetime import datetime, timedelta, timezone

    from app.core.config import NotificationConfig, TelegramConfig, WebhookConfig
    from app.core.notifications import NotificationManager

    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
        )
    )
    spy = _Spy()
    manager.register("spy", spy)

    ended = _ended_event()
    active = {k: v for k, v in ended.items() if k != "end_time"}
    t0 = datetime(2026, 11, 15, 12, 40, tzinfo=timezone.utc)

    monitor = AwsStatusMonitor()
    monitor.configure(_config(service_filter=["us-east-1"]))

    async def cycle(feed, minute):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=feed)
        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        result = await monitor.check(http_client=client, logger=logging.getLogger("test"))
        await manager.handle_result(
            module_id="aws",
            result=result,
            module_config=_config(service_filter=["us-east-1"]),
            level_name="WARNING",
            event_name="monitor_check",
            event_time=t0 + timedelta(minutes=minute),
            http_client=AsyncMock(),
            logger=MagicMock(spec=logging.Logger),
        )
        return result.status

    async def scenario():
        assert await cycle([active], 0) == MonitorStatus.ALERT
        assert await cycle([ended], 1) == MonitorStatus.OK
        assert await cycle([ended], 2) == MonitorStatus.OK

    asyncio.run(scenario())
    assert len(spy.alerts) == 1
    assert len(spy.recoveries) == 1
