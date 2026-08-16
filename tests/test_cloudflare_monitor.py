"""Tests for the Cloudflare status monitor.

Cloudflare is the same Statuspage v2 shape as `github` and `bitbucket` at twenty times
the size: **475 components**, of which 128 are products and the rest one per point of
presence. The fixture is the real payload, captured on a day when 51 components were
non-operational — 33 in `partial_outage` and 18 in `under_maintenance`. Every one of
them is a PoP, and not one is a product.

That fixture is the whole point of this suite. A module that copied the Bitbucket
defaults would pass a hand-written "one component is down" test and then fire 33 alerts
across four channels on its first real cycle — 33 rather than 51 only because
`under_maintenance` is outside the default rule. The decisive test here is the
boring-looking one: the real payload, the default configuration, and `OK`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.statuspage import extract_components as _extract_components
from app.core.statuspage import slugify as _slugify
from app.core.types import MonitorStatus
from app.modules.cloudflare.monitor import (
    _DEFAULT_SERVICE_FILTER,
    CloudflareStatusMonitor,
    get_monitor,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "cloudflare" / "summary.json"
_URL = "https://www.cloudflarestatus.com/api/v2/summary.json"


def _real_payload() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _config(service_filter=None, rule_value="degraded_performance,partial_outage,major_outage",
            rule_kind="status", url=_URL):
    return ModuleConfig(
        slug="cloudflare",
        url=url,
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=list(service_filter or []),
        enabled=True,
    )


def _client(payload, *, extra=None, status_code=200, raises=None):
    """An http client returning `payload` for summary and `extra` for enrichment."""
    def _response(body):
        response = MagicMock()
        response.status_code = status_code
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=body)
        return response

    client = MagicMock()
    if raises is not None:
        client.get = AsyncMock(side_effect=raises)
        return client

    async def get(url, **_kwargs):
        if "/api/v2/summary.json" in url:
            return _response(payload)
        return _response(extra if extra is not None else {})

    client.get = get
    return client


def _logger():
    return MagicMock(spec=logging.Logger)


async def _check(payload, config=None, **client_kwargs):
    monitor = get_monitor()
    monitor.configure(config or _config())
    return await monitor.check(http_client=_client(payload, **client_kwargs), logger=_logger())


def _component(name, status="operational", cid=None):
    return {"id": cid or name.lower().replace(" ", ""), "name": name, "status": status}


# ---------------------------------------------------------------------------
# The real payload — the reason this module needed a default of its own
# ---------------------------------------------------------------------------

def test_the_fixture_is_the_real_thing_and_is_as_large_as_claimed():
    components = _real_payload()["components"]
    assert len(components) == 475
    # Captured on a day with 51 degraded components. The count matters only as
    # evidence that this is a stressful payload, not a hand-picked calm one.
    assert sum(1 for c in components if c["status"] != "operational") == 51


def test_the_degraded_components_in_the_fixture_are_all_points_of_presence():
    """If a product were degraded too, the next test would pass for the wrong reason."""
    data = _real_payload()
    groups = {c["id"]: c["name"] for c in data["components"] if c.get("group")}
    degraded = [c for c in data["components"] if c["status"] != "operational"]
    products = [c for c in degraded if groups.get(c.get("group_id")) == "Cloudflare Sites and Services"]
    assert products == []


@pytest.mark.asyncio
async def test_the_real_payload_with_default_config_produces_no_alert():
    """The decisive one: 51 non-operational PoPs upstream, and the on-call sleeps."""
    result = await _check(_real_payload())
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_the_default_watches_exactly_the_curated_list():
    result = await _check(_real_payload())
    assert {c["name"] for c in result.payload} == set(_DEFAULT_SERVICE_FILTER)


@pytest.mark.asyncio
async def test_watching_everything_on_the_real_payload_alerts_on_every_match():
    """The behaviour the default exists to avoid — available, but opt-in.

    33 and not 51: the other 18 are `under_maintenance`, which the default rule
    deliberately does not treat as an outage.
    """
    result = await _check(_real_payload(), _config(service_filter=["*"]))
    assert result.status == MonitorStatus.ALERT
    assert len(result.payload) == 33


@pytest.mark.asyncio
async def test_every_default_name_matches_a_component_in_the_real_payload():
    """A typo here would silently shrink the watchlist to the names that do match."""
    names = {c["name"] for c in _real_payload()["components"]}
    assert set(_DEFAULT_SERVICE_FILTER) <= names


def test_no_two_components_share_a_name_in_the_real_payload():
    """Why the allowlist can key on names and needs no group filtering."""
    names = [c["name"].lower() for c in _real_payload()["components"]]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_watched_product_going_down_alerts():
    data = _real_payload()
    for comp in data["components"]:
        if comp["name"] == "Tunnel":
            comp["status"] = "major_outage"
    result = await _check(data)
    assert result.status == MonitorStatus.ALERT
    assert "Tunnel" in result.reason


@pytest.mark.asyncio
async def test_the_alert_payload_carries_only_the_degraded_component():
    """The contract the notification state machine reconciles against."""
    data = _real_payload()
    for comp in data["components"]:
        if comp["name"] == "Tunnel":
            comp["status"] = "major_outage"
    result = await _check(data)
    assert [c["name"] for c in result.payload] == ["Tunnel"]


@pytest.mark.asyncio
async def test_a_product_outside_the_allowlist_going_down_does_not_alert():
    data = _real_payload()
    for comp in data["components"]:
        if comp["name"] == "Stream":
            comp["status"] = "major_outage"
    result = await _check(data)
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_a_point_of_presence_going_down_does_not_alert():
    data = _real_payload()
    for comp in data["components"]:
        if comp["name"].startswith("Sao Paulo") or comp["name"].startswith("São Paulo"):
            comp["status"] = "major_outage"
    result = await _check(data)
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_two_watched_products_down_are_both_named():
    data = _real_payload()
    for comp in data["components"]:
        if comp["name"] in {"Tunnel", "Authoritative DNS"}:
            comp["status"] = "partial_outage"
    result = await _check(data)
    assert "Tunnel" in result.reason and "Authoritative DNS" in result.reason
    assert len(result.payload) == 2


@pytest.mark.asyncio
async def test_a_status_outside_the_rule_does_not_alert():
    data = {"components": [_component("Tunnel", "under_maintenance")]}
    result = await _check(data)
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_the_rule_value_is_configurable():
    data = {"components": [_component("Tunnel", "under_maintenance")]}
    result = await _check(data, _config(rule_value="under_maintenance"))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_an_empty_rule_value_falls_back_to_the_three_outage_states():
    data = {"components": [_component("Tunnel", "major_outage")]}
    result = await _check(data, _config(rule_value=""))
    assert result.status == MonitorStatus.ALERT


# ---------------------------------------------------------------------------
# The watchlist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_explicit_filter_replaces_the_default_entirely():
    data = {"components": [_component("Tunnel", "major_outage"), _component("R2", "major_outage")]}
    result = await _check(data, _config(service_filter=["R2"]))
    assert [c["name"] for c in result.payload] == ["R2"]


@pytest.mark.asyncio
async def test_the_filter_matches_by_name_case_insensitively():
    data = {"components": [_component("Tunnel", "major_outage")]}
    result = await _check(data, _config(service_filter=["tUnNeL"]))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_the_filter_matches_by_id():
    data = {"components": [{"id": "abc123", "name": "Tunnel", "status": "major_outage"}]}
    result = await _check(data, _config(service_filter=["abc123"]))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_the_filter_matches_by_slug():
    data = {"components": [{"id": "x", "name": "CDN/Cache", "status": "major_outage"}]}
    result = await _check(data, _config(service_filter=["cdn-cache"]))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_whitespace_around_a_filter_entry_is_ignored():
    data = {"components": [_component("Tunnel", "major_outage")]}
    result = await _check(data, _config(service_filter=["  Tunnel  "]))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_a_filter_of_only_blanks_falls_back_to_the_default():
    data = {"components": [_component("Tunnel", "major_outage"), _component("R2", "major_outage")]}
    result = await _check(data, _config(service_filter=["   ", ""]))
    assert [c["name"] for c in result.payload] == ["Tunnel"]


@pytest.mark.asyncio
async def test_the_star_escape_hatch_works_alongside_other_entries():
    data = {"components": [_component("Tunnel", "major_outage"), _component("R2", "major_outage")]}
    result = await _check(data, _config(service_filter=["R2", "*"]))
    assert len(result.payload) == 2


@pytest.mark.asyncio
async def test_a_filter_matching_nothing_is_an_error_not_a_silent_ok():
    data = {"components": [_component("Tunnel")]}
    result = await _check(data, _config(service_filter=["Nonexistent Product"]))
    assert result.status == MonitorStatus.ERROR
    assert "no target components matched filter" in result.reason


@pytest.mark.asyncio
async def test_the_unmatched_filter_error_reports_what_was_asked_for():
    data = {"components": [_component("Tunnel")]}
    result = await _check(data, _config(service_filter=["Nope"]))
    assert result.payload["filter"] == ["nope"]


# ---------------------------------------------------------------------------
# A renamed component must not shrink the watchlist in silence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_watched_name_absent_from_the_payload_is_logged():
    monitor = get_monitor()
    monitor.configure(_config(service_filter=["Tunnel", "Renamed Away"]))
    logger = _logger()
    await monitor.check(
        http_client=_client({"components": [_component("Tunnel")]}), logger=logger
    )
    warnings = [
        call for call in logger.warning.call_args_list
        if call[0][0] == "watched component not found in status payload"
    ]
    assert len(warnings) == 1
    assert "renamed away" in warnings[0][1]["extra"]["reason"]


@pytest.mark.asyncio
async def test_a_watchlist_fully_present_logs_nothing():
    monitor = get_monitor()
    monitor.configure(_config(service_filter=["Tunnel"]))
    logger = _logger()
    await monitor.check(
        http_client=_client({"components": [_component("Tunnel")]}), logger=logger
    )
    assert logger.warning.call_args_list == []


@pytest.mark.asyncio
async def test_the_default_watchlist_is_fully_present_in_the_real_payload():
    monitor = get_monitor()
    monitor.configure(_config())
    logger = _logger()
    await monitor.check(http_client=_client(_real_payload()), logger=logger)
    assert logger.warning.call_args_list == []


@pytest.mark.asyncio
async def test_watching_everything_never_warns_about_missing_names():
    monitor = get_monitor()
    monitor.configure(_config(service_filter=["*"]))
    logger = _logger()
    await monitor.check(
        http_client=_client({"components": [_component("Tunnel")]}), logger=logger
    )
    assert logger.warning.call_args_list == []


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_open_incident_enriches_the_alert_reason():
    data = {"components": [_component("Tunnel", "major_outage")]}
    extra = {"incidents": [{"name": "Tunnel connectivity", "status": "investigating",
                            "updated_at": "2026-08-16T12:00:00Z"}]}
    result = await _check(data, extra=extra)
    assert "Incident: Tunnel connectivity (investigating, 2026-08-16T12:00:00Z)" in result.reason


@pytest.mark.asyncio
async def test_enrichment_is_capped_at_three_incidents():
    data = {"components": [_component("Tunnel", "major_outage")]}
    extra = {"incidents": [{"name": f"i{i}", "status": "investigating"} for i in range(9)]}
    result = await _check(data, extra=extra)
    assert result.reason.count("Incident:") == 3


@pytest.mark.asyncio
async def test_reason_items_keep_one_entry_per_finding():
    """The reason cannot be split back apart: incident titles contain commas."""
    data = {"components": [_component("Tunnel", "major_outage")]}
    extra = {"incidents": [{"name": "Down in Frankfurt, Berlin; and Munich",
                            "status": "identified"}]}
    result = await _check(data, extra=extra)
    assert len(result.reason_items) == 2


@pytest.mark.asyncio
async def test_no_enrichment_leaves_the_reason_byte_identical():
    data = {"components": [_component("Tunnel", "major_outage")]}
    result = await _check(data, extra={"incidents": [], "scheduled_maintenances": []})
    assert result.reason == "Tunnel: major_outage"


@pytest.mark.asyncio
async def test_a_failing_enrichment_fetch_still_alerts():
    data = {"components": [_component("Tunnel", "major_outage")]}

    monitor = get_monitor()
    monitor.configure(_config())

    async def get(url, **_kwargs):
        if "/api/v2/summary.json" in url:
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.json = MagicMock(return_value=data)
            return response
        raise httpx_error()

    def httpx_error():
        return RuntimeError("enrichment endpoint down")

    client = MagicMock()
    client.get = get
    result = await monitor.check(http_client=client, logger=_logger())
    assert result.status == MonitorStatus.ALERT
    assert result.reason == "Tunnel: major_outage"


# ---------------------------------------------------------------------------
# Rule strategies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_keyword_rule_matches_anywhere_in_the_body():
    data = {"components": [_component("Tunnel")], "status": {"description": "Major Outage"}}
    result = await _check(data, _config(rule_kind="keyword", rule_value="major outage"))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_a_keyword_rule_that_does_not_match_is_ok():
    data = {"components": [_component("Tunnel")]}
    result = await _check(data, _config(rule_kind="keyword", rule_value="catastrophe"))
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_a_regex_rule_matches():
    data = {"components": [_component("Tunnel", "partial_outage")]}
    result = await _check(data, _config(rule_kind="regex", rule_value=r"partial_\w+"))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_an_invalid_regex_is_an_error():
    data = {"components": [_component("Tunnel")]}
    result = await _check(data, _config(rule_kind="regex", rule_value="[unclosed"))
    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in result.reason


@pytest.mark.asyncio
async def test_an_unknown_rule_kind_is_an_error():
    data = {"components": [_component("Tunnel")]}
    result = await _check(data, _config(rule_kind="telepathy"))
    assert result.status == MonitorStatus.ERROR
    assert "unsupported rule kind" in result.reason


# ---------------------------------------------------------------------------
# Failure paths — never raise into the scheduler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_network_failure_is_an_error_not_an_exception():
    result = await _check(None, raises=RuntimeError("connection reset"))
    assert result.status == MonitorStatus.ERROR
    assert "connection reset" in result.reason


@pytest.mark.asyncio
async def test_a_timeout_is_an_error():
    result = await _check(None, raises=TimeoutError("timed out"))
    assert result.status == MonitorStatus.ERROR


@pytest.mark.asyncio
async def test_a_payload_without_components_is_an_error():
    result = await _check({"page": {}})
    assert result.status == MonitorStatus.ERROR
    assert "no components in status response" in result.reason


@pytest.mark.asyncio
async def test_an_empty_component_list_is_an_error():
    result = await _check({"components": []})
    assert result.status == MonitorStatus.ERROR


@pytest.mark.asyncio
async def test_an_unconfigured_monitor_refuses_to_run():
    monitor = CloudflareStatusMonitor()
    with pytest.raises(RuntimeError, match="not configured"):
        await monitor.check(http_client=MagicMock(), logger=_logger())


@pytest.mark.asyncio
async def test_the_duration_is_always_reported():
    result = await _check(_real_payload())
    assert result.duration_ms is not None and result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Bug-Hunter — hostile payloads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_component_without_a_name_does_not_crash():
    result = await _check({"components": [{"id": "x", "status": "major_outage"}]},
                          _config(service_filter=["unknown"]))
    assert result.status == MonitorStatus.ALERT


@pytest.mark.asyncio
async def test_a_component_without_a_status_is_treated_as_unknown_not_degraded():
    result = await _check({"components": [{"id": "x", "name": "Tunnel"}]})
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_a_component_without_an_id_falls_back_to_its_slug():
    components = _extract_components({"components": [{"name": "CDN/Cache", "status": "operational"}]})
    assert components[0]["id"] == "cdn-cache"


@pytest.mark.asyncio
async def test_an_unknown_status_value_does_not_alert_under_the_default_rule():
    """Statuspage could add a state; an open set must not be read as an outage."""
    result = await _check({"components": [_component("Tunnel", "brand_new_state")]})
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_components_being_a_dict_instead_of_a_list_is_an_error():
    result = await _check({"components": {"nope": True}})
    assert result.status == MonitorStatus.ERROR


@pytest.mark.asyncio
async def test_a_deeply_nested_payload_does_not_recurse():
    """Small input, hostile shape: 1000 levels of nesting under an unread key."""
    nested = {}
    cursor = nested
    for _ in range(1000):
        cursor["next"] = {}
        cursor = cursor["next"]
    result = await _check({"components": [_component("Tunnel")], "junk": nested})
    assert result.status == MonitorStatus.OK


@pytest.mark.asyncio
async def test_a_duplicated_component_name_alerts_once_per_occurrence():
    """Distinctness: two entries are two components even with an identical name."""
    data = {"components": [
        {"id": "a", "name": "Tunnel", "status": "major_outage"},
        {"id": "b", "name": "Tunnel", "status": "major_outage"},
    ]}
    result = await _check(data)
    assert len(result.payload) == 2
    assert {c["id"] for c in result.payload} == {"a", "b"}


def test_slugify_collapses_punctuation():
    assert _slugify("CDN/Cache") == "cdn-cache"
    assert _slugify("Bring Your Own IP (BYOIP)") == "bring-your-own-ip-byoip"


def test_get_monitor_honours_a_custom_slug():
    assert get_monitor("cf-mirror").id == "cf-mirror"
