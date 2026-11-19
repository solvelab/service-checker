"""Tests for the ElevenLabs status monitor (#117).

`summary_incident.json` is the live payload of 2026-11-18 (indicator `minor`, one open
incident, no components). `summary_healthy.json` is the same page with the indicator
set to `none` and the incident gone: the page never showed a healthy form while this
module was written.
"""
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
from app.modules.elevenlabs.monitor import ElevenLabsStatusMonitor, get_monitor

pytestmark = pytest.mark.asyncio

_FIXTURES = Path(__file__).parent / "fixtures" / "elevenlabs"
_INCIDENT = "Increased latency and API errors"
_INCIDENT_ID = "01M7QMQH05JXV3SKSM1H6PJZD2"


def _load(name: str) -> dict:
    return copy.deepcopy(json.loads((_FIXTURES / name).read_text()))


def _config(rule_kind="status", rule_value="major,minor", service_filter=None):
    return ModuleConfig(
        slug="elevenlabs",
        url="https://status.elevenlabs.io/api/v2/summary.json",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _client(payload=None, exc=None):
    http_client = AsyncMock(spec=httpx.AsyncClient)
    if exc is not None:
        http_client.get.side_effect = exc
        return http_client
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    http_client.get.return_value = resp
    return http_client


async def _check(payload, **config):
    monitor = ElevenLabsStatusMonitor()
    monitor.configure(_config(**config))
    return await monitor.check(_client(payload), MagicMock(spec=logging.Logger))


# ---------------------------------------------------------------------------
# The real page
# ---------------------------------------------------------------------------

async def test_the_live_incident_alerts_as_the_page_and_the_incident():
    result = await _check(_load("summary_incident.json"))

    assert result.status == MonitorStatus.ALERT
    assert result.message == "elevenlabs status degraded"
    page, incident = result.payload
    assert page == {"id": "page", "name": "ElevenLabs", "status": "minor",
                    "status_text": "Partial System Degradation",
                    "incidents": ["Increased latency and API errors"]}
    assert incident["id"] == f"incident:{_INCIDENT_ID}"
    assert incident["name"] == _INCIDENT
    assert incident["status"] == "identified"
    assert incident["impact"] == "minor"
    assert result.reason_items == [
        "ElevenLabs: Partial System Degradation", f"{_INCIDENT}: identified"]


async def test_the_healthy_page_is_ok_without_components():
    result = await _check(_load("summary_healthy.json"))

    assert result.status == MonitorStatus.OK
    assert result.payload == []


async def test_a_raised_indicator_with_no_open_incident_alerts_as_the_page():
    data = _load("summary_incident.json")
    data["incidents"] = []
    result = await _check(data)

    assert result.status == MonitorStatus.ALERT
    assert [item["id"] for item in result.payload] == ["page"]


async def test_an_open_incident_alerts_even_with_the_indicator_none():
    data = _load("summary_incident.json")
    data["status"]["indicator"] = "none"
    result = await _check(data)

    assert [item["id"] for item in result.payload] == [f"incident:{_INCIDENT_ID}"]


async def test_a_resolved_incident_is_not_an_item():
    data = _load("summary_incident.json")
    data["incidents"][0]["status"] = "resolved"
    result = await _check(data)

    assert [item["id"] for item in result.payload] == ["page"]


async def test_a_resolved_status_with_whitespace_is_closed():
    data = _load("summary_incident.json")
    data["incidents"][0]["status"] = "RESOLVED "
    result = await _check(data)

    assert [item["id"] for item in result.payload] == ["page"]


# ---------------------------------------------------------------------------
# The severity threshold
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule_value, indicator, expected", [
    ("major,minor", "minor", MonitorStatus.ALERT),   # the generic default
    ("major,minor", "critical", MonitorStatus.ALERT),
    ("major", "minor", MonitorStatus.OK),
    ("major", "major", MonitorStatus.ALERT),
    ("major", "critical", MonitorStatus.ALERT),
    ("critical", "major", MonitorStatus.OK),
    ("", "minor", MonitorStatus.ALERT),             # no severity named: minor and up
    ("degraded_performance", "minor", MonitorStatus.ALERT),
    ("major,minor", "none", MonitorStatus.OK),
    ("none,major", "none", MonitorStatus.OK),        # `none` never alerts
    ("major", "maintenance", MonitorStatus.OK),     # not a severity
    ("maintenance", "maintenance", MonitorStatus.ALERT),  # named explicitly
    (" MAJOR ", "Major", MonitorStatus.ALERT),
])
async def test_the_rule_value_is_the_minimum_severity(rule_value, indicator, expected):
    data = _load("summary_healthy.json")
    data["status"]["indicator"] = indicator
    result = await _check(data, rule_value=rule_value)

    assert result.status == expected


# ---------------------------------------------------------------------------
# Published components (none today)
# ---------------------------------------------------------------------------

async def test_a_degraded_component_alerts_even_with_the_indicator_none():
    data = _load("summary_healthy.json")
    data["components"] = [
        {"id": "tts", "name": "Text to Speech", "status": "partial_outage"},
        {"id": "stt", "name": "Speech to Text", "status": "operational"},
    ]
    result = await _check(data)

    assert result.status == MonitorStatus.ALERT
    assert [item["id"] for item in result.payload] == ["component:tts"]


async def test_published_components_are_not_filtered_by_region():
    # SERVICE_FILTER names regions since #136; a component is never a region.
    data = _load("summary_healthy.json")
    data["components"] = [
        {"id": "tts", "name": "Text to Speech", "status": "partial_outage"},
    ]
    result = await _check(data)
    assert result.status == MonitorStatus.ALERT
    assert [i["id"] for i in result.payload] == ["component:tts"]


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    None, [], "html", {"page": {}}, {"status": {}}, {"status": {"indicator": None}},
    {"status": "minor"},
])
async def test_a_missing_indicator_is_an_error(payload):
    result = await _check(payload)

    assert result.status == MonitorStatus.ERROR
    assert result.reason == "no status indicator in status response"


@pytest.mark.parametrize("indicator", ["", "  ", "partial_outage", "degraded"])
async def test_an_empty_or_unknown_indicator_is_an_error(indicator):
    # Read as OK it would also announce the open incident restored (bug-hunter).
    data = _load("summary_incident.json")
    data["status"]["indicator"] = indicator
    result = await _check(data)

    assert result.status == MonitorStatus.ERROR
    assert "unknown status indicator" in result.reason


@pytest.mark.parametrize("incidents", [None, {"a": 1}, ["boom", None], [{"name": None}]])
async def test_malformed_incidents_do_not_raise(incidents):
    data = _load("summary_incident.json")
    data["incidents"] = incidents
    result = await _check(data)

    assert result.status == MonitorStatus.ALERT


async def test_a_request_failure_is_an_error():
    monitor = ElevenLabsStatusMonitor()
    monitor.configure(_config())
    result = await monitor.check(
        _client(exc=httpx.ConnectTimeout("timed out")), MagicMock(spec=logging.Logger))

    assert result.status == MonitorStatus.ERROR
    assert result.message == "elevenlabs status request failed"
    assert "timed out" in result.reason
    assert result.duration_ms is not None


async def test_keyword_and_regex_rules():
    data = _load("summary_incident.json")
    assert (await _check(data, rule_kind="keyword", rule_value="latency")).status \
        == MonitorStatus.ALERT
    assert (await _check(data, rule_kind="regex", rule_value="(")).status \
        == MonitorStatus.ERROR
    assert (await _check(data, rule_kind="bogus")).status == MonitorStatus.ERROR


async def test_an_unconfigured_monitor_raises():
    with pytest.raises(RuntimeError):
        await ElevenLabsStatusMonitor().check(_client({}), MagicMock())


async def test_the_factory_and_the_default_url(monkeypatch):
    from app.core.config import _load_module_config, load_app_config

    assert get_monitor().id == "elevenlabs"
    monkeypatch.delenv("ELEVENLABS_URL", raising=False)
    config = _load_module_config("elevenlabs", load_app_config().defaults)
    assert config.url == "https://status.elevenlabs.io/api/v2/summary.json"


# ---------------------------------------------------------------------------
# Through the NotificationManager (bug-hunter): one outage, one story
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.core.config import NotificationConfig, TelegramConfig, WebhookConfig  # noqa: E402
from app.core.notifications import NotificationManager  # noqa: E402

_T0 = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)


class _Spy:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def _record(self, kind, kwargs):
        self.calls.append((kind, kwargs["result"].reason))
        return True

    async def send_alert(self, **kwargs):
        return await self._record("alert", kwargs)

    async def send_recovery(self, **kwargs):
        return await self._record("recovery", kwargs)

    async def send_monitor_error(self, **kwargs):
        return await self._record("monitor_error", kwargs)

    async def send_monitor_recovered(self, **kwargs):
        return await self._record("monitor_recovered", kwargs)


def _page(indicator, *incidents):
    data = _load("summary_healthy.json")
    data["status"]["indicator"] = indicator
    data["incidents"] = [
        {"id": iid, "name": f"Incident {iid}", "status": status, "impact": impact}
        for iid, status, impact in incidents
    ]
    return data


async def _drive(pages, rule_value="minor"):
    manager = NotificationManager(NotificationConfig(
        telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10, error_threshold=3, recovery_threshold=3,
    ))
    spy = _Spy()
    manager.register("spy", spy)
    monitor = ElevenLabsStatusMonitor()
    config = _config(rule_value=rule_value)
    monitor.configure(config)
    for minute, data in enumerate(pages):
        result = await monitor.check(_client(data), MagicMock(spec=logging.Logger))
        await manager.handle_result(
            module_id="elevenlabs", result=result, module_config=config,
            level_name="INFO", event_name="monitor_check",
            event_time=_T0 + timedelta(minutes=minute),
            http_client=AsyncMock(), logger=logging.getLogger("test.elevenlabs"),
        )
    return [kind for kind, _ in spy.calls], spy.calls


async def test_an_incident_resolved_before_the_indicator_drops_is_one_story():
    kinds, calls = await _drive([
        _page("minor", ("I1", "investigating", "minor")),
        _page("minor", ("I1", "resolved", "minor")),
        _page("none"),
    ])

    # The page and the incident each alert once and recover once; nothing re-alerts.
    assert kinds == ["alert", "alert", "recovery", "recovery"]


async def test_an_incident_opening_under_a_raised_page_does_not_recover_the_page():
    kinds, _ = await _drive([
        _page("minor"),
        _page("minor", ("I1", "investigating", "minor")),
    ])

    assert kinds == ["alert", "alert"]


async def test_an_open_incident_is_not_restored_when_the_indicator_drops():
    kinds, _ = await _drive([
        _page("major", ("I1", "investigating", "major")),
        _page("minor", ("I1", "investigating", "major")),
        _page("maintenance", ("I1", "investigating", "major")),
    ], rule_value="major")

    # The page item recovers with the indicator; the open major incident does not.
    assert kinds == ["alert", "alert", "recovery"]


async def test_an_incident_with_id_page_is_not_mistaken_for_the_page_item():
    kinds, _ = await _drive([_page("minor"), _page("minor", ("page", "investigating", "minor"))])

    assert kinds == ["alert", "alert"]


# ---------------------------------------------------------------------------
# Regions (#136): the live sub-page catalogue and regional listings of 2026-11-19
# ---------------------------------------------------------------------------

_CATALOG = _FIXTURES / "proxy_catalog.json"
_REGIONAL = _FIXTURES / "regional_incidents.json"
_VOICE_ID = "01M7SND7J0PCKGN45B3FADECJR"
_VOICE = "Degraded performance of Voice Library voices"


def _voice_summary():
    data = _load("summary_incident.json")
    data["incidents"].append({"id": _VOICE_ID, "name": _VOICE, "status": "investigating",
                              "impact": "minor", "updated_at": "2026-11-19T10:25:02Z"})
    return data


def _routing_client(summary, catalog=None, regional=None, fail=None):
    """Answer by URL like the real host; `fail` maps a URL fragment to an exception."""
    catalog = catalog if catalog is not None else json.loads(_CATALOG.read_text())
    regional = regional if regional is not None else json.loads(_REGIONAL.read_text())
    calls = []

    async def get(url, params=None, **_):
        slug = (params or {}).get("subpage_slug")
        calls.append((url, slug))
        for fragment, exc in (fail or {}).items():
            if fragment in url or fragment == f"region:{slug}":
                raise exc
        if url.endswith("/api/v2/summary.json"):
            body = summary
        elif url.endswith("/incidents"):
            body = regional.get(params["subpage_slug"], {"incidents": []})
        else:
            body = catalog
        resp = MagicMock(spec=httpx.Response)
        resp.json.return_value = copy.deepcopy(body)
        resp.raise_for_status.return_value = None
        return resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = get
    return client, calls


async def _region_check(summary, logger=None, **kwargs):
    config = {k: kwargs.pop(k) for k in ("service_filter", "rule_value") if k in kwargs}
    monitor = ElevenLabsStatusMonitor()
    monitor.configure(_config(**config))
    client, calls = _routing_client(summary, **kwargs)
    result = await monitor.check(client, logger or MagicMock(spec=logging.Logger))
    return result, calls


def _incident(result, incident_id):
    return next(i for i in result.payload if i["id"] == f"incident:{incident_id}")


async def test_each_incident_names_its_regions():
    result, calls = await _region_check(_voice_summary())
    assert result.status == MonitorStatus.ALERT
    voice = _incident(result, _VOICE_ID)
    assert voice["regions"] == ["EU (Enterprise-only)", "India (Enterprise-only)",
                                "Singapore (Enterprise-only)"]
    assert (f"{_VOICE} [EU (Enterprise-only), India (Enterprise-only), "
            f"Singapore (Enterprise-only)]: investigating") in result.reason_items
    us = _incident(result, _INCIDENT_ID)
    assert us["regions"] == ["Global (United States)"]
    # one item per incident, not one per region
    assert sum(i["id"] == f"incident:{_VOICE_ID}" for i in result.payload) == 1
    assert len(calls) == 1 + 1 + 4  # summary, catalogue, four regions


async def test_a_healthy_page_costs_no_extra_request():
    result, calls = await _region_check(_load("summary_healthy.json"))
    assert result.status == MonitorStatus.OK
    assert [url for url, _ in calls] == ["https://status.elevenlabs.io/api/v2/summary.json"]


async def test_a_filter_leaves_out_an_incident_of_other_regions_and_the_page():
    result, _ = await _region_check(_voice_summary(), service_filter=["united-states"])
    assert result.status == MonitorStatus.ALERT
    assert [i["id"] for i in result.payload] == [f"incident:{_INCIDENT_ID}"]


async def test_a_filter_matches_by_name_too_and_ignores_case():
    result, _ = await _region_check(_voice_summary(), service_filter=["india (ENTERPRISE-ONLY)"])
    assert [i["id"] for i in result.payload] == [f"incident:{_VOICE_ID}"]


async def test_a_filter_with_only_other_regions_affected_is_ok():
    summary = _load("summary_healthy.json")
    summary["status"]["indicator"] = "minor"  # the page item would alert without a filter
    summary["incidents"] = [{"id": _VOICE_ID, "name": _VOICE, "status": "investigating",
                             "impact": "minor"}]
    result, _ = await _region_check(summary, service_filter=["united-states"])
    assert result.status == MonitorStatus.OK


async def test_an_unknown_region_in_the_filter_is_an_error():
    result, _ = await _region_check(_load("summary_healthy.json"), service_filter=["brazil"])
    assert result.status == MonitorStatus.ERROR
    assert "brazil" in result.reason and "united-states" in result.reason


@pytest.mark.parametrize("fail, catalog", [
    ({"/proxy/status.elevenlabs.io/incidents": httpx.ConnectError("down")}, None),
    ({"/proxy/": httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())}, None),
    ({}, {"summary": {}}),  # the catalogue changed shape
    ({}, {"subpages": [{"name": "no subpath"}]}),
])
async def test_a_failed_lookup_alerts_without_region_and_warns(fail, catalog):
    logger = MagicMock(spec=logging.Logger)
    result, _ = await _region_check(_voice_summary(), logger=logger, fail=fail, catalog=catalog)
    assert result.status == MonitorStatus.ALERT
    voice = _incident(result, _VOICE_ID)
    assert "regions" not in voice and voice["name"] == _VOICE
    logger.warning.assert_called_once()
    assert logger.warning.call_args.kwargs["extra"]["event"] == "region_lookup_failed"


async def test_with_a_filter_a_failed_lookup_still_alerts():
    result, _ = await _region_check(
        _voice_summary(), service_filter=["united-states"],
        fail={"/proxy/": httpx.ConnectError("down")},
    )
    assert {i["id"] for i in result.payload} == {f"incident:{_INCIDENT_ID}", f"incident:{_VOICE_ID}"}


async def test_an_incident_missing_from_every_region_alerts_without_region():
    summary = _load("summary_healthy.json")
    summary["incidents"] = [{"id": "NEW", "name": "Brand new", "status": "investigating",
                             "impact": "minor"}]
    result, _ = await _region_check(summary, service_filter=["united-states"])
    assert result.status == MonitorStatus.ALERT
    assert result.payload[0]["name"] == "Brand new"



class _Cycles:
    """One monitor across several checks, each with its own regional answers."""

    def __init__(self, **config):
        self.monitor = ElevenLabsStatusMonitor()
        self.monitor.configure(_config(**config))
        self.logger = MagicMock(spec=logging.Logger)

    async def run(self, summary, **kwargs):
        client, calls = _routing_client(summary, **kwargs)
        return await self.monitor.check(client, self.logger)


def _ids(result):
    return [i["id"] for i in (result.payload or [])]


async def test_a_flapping_lookup_never_lets_in_an_incident_outside_the_filter():
    cycles = _Cycles(service_filter=["united-states"])
    voice = f"incident:{_VOICE_ID}"
    for fail in ({}, {"region:singapore": httpx.ConnectError("x")}, {"/proxy/": httpx.ConnectError("x")}, {}):
        result = await cycles.run(_voice_summary(), fail=fail)
        assert voice not in _ids(result)
        assert f"incident:{_INCIDENT_ID}" in _ids(result)


async def test_an_incident_let_in_before_its_region_was_known_stays_in():
    # summary.json lists it before any regional listing does: let in (unknown region),
    # and kept when the listing shows it outside the filter — no false "restored".
    cycles = _Cycles(service_filter=["united-states"])
    regional = json.loads(_REGIONAL.read_text())
    lagging = {slug: {"incidents": [i for i in body["incidents"] if i["id"] != _VOICE_ID]}
               for slug, body in regional.items()}
    first = await cycles.run(_voice_summary(), regional=lagging)
    second = await cycles.run(_voice_summary())
    voice = f"incident:{_VOICE_ID}"
    assert voice in _ids(first) and voice in _ids(second)
    assert "EU (Enterprise-only)" in _incident(second, _VOICE_ID)["name"]


async def test_one_failing_region_keeps_the_others_and_regions_stay_known():
    cycles = _Cycles()
    partial = await cycles.run(_voice_summary(), fail={"region:singapore": httpx.ConnectError("x")})
    assert _incident(partial, _VOICE_ID)["regions"] == ["EU (Enterprise-only)", "India (Enterprise-only)"]
    full = await cycles.run(_voice_summary())
    down = await cycles.run(_voice_summary(), fail={"/proxy/": httpx.ConnectError("x")})
    for result in (full, down):
        assert _incident(result, _VOICE_ID)["regions"] == [
            "EU (Enterprise-only)", "India (Enterprise-only)", "Singapore (Enterprise-only)"]


async def test_a_failing_lookup_is_logged_once_per_streak_and_its_recovery_once():
    cycles = _Cycles()
    for _ in range(3):
        await cycles.run(_voice_summary(), fail={"/proxy/": httpx.ConnectError("x")})
    await cycles.run(_voice_summary())
    await cycles.run(_voice_summary())
    assert cycles.logger.warning.call_count == 1
    events = [c.kwargs["extra"]["event"] for c in cycles.logger.info.call_args_list]
    assert events == ["region_lookup_recovered"]


async def test_an_incident_below_the_rule_costs_no_regional_request():
    result, calls = await _region_check(_voice_summary(), rule_value="critical")
    assert result.status == MonitorStatus.OK
    assert len(calls) == 1


async def test_a_closed_incident_forgets_its_regions_and_decision():
    cycles = _Cycles(service_filter=["united-states"])
    await cycles.run(_voice_summary())
    assert _VOICE_ID in cycles.monitor._decisions
    await cycles.run(_load("summary_healthy.json"))
    assert cycles.monitor._decisions == {} and cycles.monitor._known_regions == {}
