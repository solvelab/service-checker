"""Tests for the Rockstar Services monitor.

Two things only this module does, and both need holding down. It fetches through TLS
impersonation, because the page sits behind a WAF that refuses ordinary HTTP clients
whatever headers they send. And it is the only monitor that can alert on an *aggregate*
state — the page-level hero — rather than on an individual service.

That second one carries an asymmetry that is easy to break by accident: the hero alerts
only when no filter is configured. With a filter, the operator asked about specific
services, and the aggregate must not override that.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.rockstar.monitor import (
    _HERO_HEADING_RE,
    RockstarStatusMonitor,
    _apply_filter,
    _classify,
    _extract_services,
    _first_phrase,
    _slugify,
    _split_item,
    _strip_residue,
    get_monitor,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rockstar"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _make_config(
    service_filter: list[str] | None = None,
    timeout: float = 15.0,
) -> ModuleConfig:
    return ModuleConfig(
        slug="rockstar",
        url="https://support.rockstargames.com/servicestatus",
        interval_seconds=60,
        timeout_seconds=timeout,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value="*"),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(monitor: RockstarStatusMonitor):
    import asyncio

    return asyncio.run(monitor.check(http_client=None, logger=logging.getLogger("test")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_slugify():
    assert _slugify("FiveM") == "fivem"
    assert _slugify("Cfx.re — Community Servers") == "cfx-re-community-servers"


def test_classify():
    assert _classify("All services operational") == "operational"
    assert _classify("There are partial outages") == "down"
    assert _classify("Scheduled maintenance ongoing") == "maintenance"
    assert _classify("") == "unknown"


def test_split_item_separates_name_and_status():
    name, status = _split_item("FiveM All services operational")
    assert name == "FiveM"
    assert status == "All services operational"


def test_split_item_no_marker_returns_text_as_name():
    name, _status = _split_item("Some Service")
    assert name == "Some Service"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_extract_services_from_real_fixture():
    html = _load("all_operational.html")
    services = _extract_services(html)
    assert len(services) == 16
    names = {s["name"] for s in services}
    assert {"FiveM", "RedM", "PC", "Rockstar Games Launcher"}.issubset(names)
    sections = {s["section"] for s in services}
    assert {"Grand Theft Auto Online", "Red Dead Online", "Online Services", "Cfx.re"}.issubset(sections)
    assert all(s["status"] == "operational" for s in services)


# ---------------------------------------------------------------------------
# End-to-end with mocked _fetch_html
# ---------------------------------------------------------------------------

def test_check_healthy_returns_ok():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.OK
    assert result.message == "rockstar status healthy"
    assert result.payload["hero"] == "All services operational"
    assert result.payload["updated_at"].startswith("As of")
    assert len(result.payload["services"]) == 16


def test_check_alert_when_filtered_service_degraded():
    """`fivem_degraded.html` is the real page with FiveM's status phrase swapped."""
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(service_filter=["FiveM"]))
    with patch.object(
        RockstarStatusMonitor, "_fetch_html", return_value=_load("fivem_degraded.html")
    ):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT
    assert "FiveM" in (result.reason or "")
    assert all(s["name"] == "FiveM" for s in result.payload["services"])
    assert result.payload["services"][0]["status"] == "down"


def test_check_filter_unmatched_returns_error():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(service_filter=["NonExistentService"]))
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "filter" in (result.reason or "").lower()


def test_check_network_failure_returns_error():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(RockstarStatusMonitor, "_fetch_html", side_effect=RuntimeError("boom")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "boom" in (result.reason or "")
    assert result.duration_ms is not None


def test_check_missing_hero_returns_error():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value="<html><body>nope</body></html>"):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "hero" in (result.reason or "").lower()


def test_get_monitor_returns_configured_slug():
    m = get_monitor("rockstar")
    assert isinstance(m, RockstarStatusMonitor)
    assert m.id == "rockstar"


def test_check_unconfigured_raises():
    monitor = RockstarStatusMonitor()
    with pytest.raises(RuntimeError):
        _run(monitor)


# ---------------------------------------------------------------------------
# _fetch_html — the transport, exercised through the client
#
# The network test above patches `_fetch_html` itself, which skips the method's own
# logic. These drive the curl_cffi client instead, so the two raise conditions inside
# it are actually executed.
# ---------------------------------------------------------------------------

def _cffi_response(status_code=200, text="<html>ok</html>"):
    return type("R", (), {"status_code": status_code, "text": text})()


def _fetch(monitor, response=None, raises=None):
    target = "curl_cffi.requests.get"
    kwargs = {"side_effect": raises} if raises else {"return_value": response}
    with patch(target, **kwargs) as mocked:
        return monitor._fetch_html(), mocked


def test_fetch_html_returns_the_body_on_success():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    body, _ = _fetch(monitor, _cffi_response(text="<html>page</html>"))
    assert body == "<html>page</html>"


def test_fetch_html_uses_impersonation_and_the_configured_timeout():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(timeout=21.0))
    _body, mocked = _fetch(monitor, _cffi_response())
    _args, kwargs = mocked.call_args
    assert kwargs["impersonate"] == monitor._impersonate
    assert kwargs["timeout"] == 21.0


def test_the_default_impersonation_profile_is_current():
    """chrome110 is already refused by another Cloudflare-fronted provider."""
    assert RockstarStatusMonitor()._impersonate == "chrome124"


def test_the_impersonation_profile_can_be_overridden(monkeypatch):
    monkeypatch.setenv("ROCKSTAR_IMPERSONATE_PROFILE", "safari17_0")
    assert RockstarStatusMonitor()._impersonate == "safari17_0"


@pytest.mark.parametrize("status", [403, 404, 500, 503])
def test_fetch_html_raises_on_an_error_status(status):
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        _fetch(monitor, _cffi_response(status_code=status))


def test_fetch_html_raises_on_an_empty_body():
    """A 200 with nothing in it is a WAF answer, not a page."""
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with pytest.raises(RuntimeError, match="empty body"):
        _fetch(monitor, _cffi_response(text=""))


def test_a_transport_error_becomes_error_not_an_exception():
    """Whatever curl_cffi raises must not reach the scheduler."""
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch("curl_cffi.requests.get", side_effect=OSError("connection reset")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "connection reset" in (result.reason or "")
    assert result.duration_ms is not None


def test_an_http_error_surfaces_the_status_in_the_reason():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch("curl_cffi.requests.get", return_value=_cffi_response(status_code=403)):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "403" in (result.reason or "")


# ---------------------------------------------------------------------------
# _evaluate — the aggregate-state asymmetry
# ---------------------------------------------------------------------------

def _with_hero(html, text):
    """Swap the hero heading's text, leaving its markup alone.

    The real element carries generated class attributes between the testid and the
    text, so this rewrites through the same regex the module parses with. A literal
    string match would silently no-op and the test would pass for the wrong reason —
    which is exactly what happened on the first attempt.
    """
    replaced, count = _HERO_HEADING_RE.subn(
        lambda m: m.group(0).replace(m.group(1), text), html, count=1
    )
    assert count == 1, "hero heading not found in fixture"
    return replaced


HERO_DEGRADED = "Some services are down"


def test_a_degraded_hero_alerts_when_no_filter_is_set():
    """The only rule that looks at the aggregate rather than at a service."""
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    html = _with_hero(_load("all_operational.html"), HERO_DEGRADED)
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT
    assert "Some services are down" in (result.reason or "")


def test_a_degraded_hero_is_ignored_when_a_filter_is_set():
    """The operator asked about specific services; the aggregate must not override that."""
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(service_filter=["FiveM"]))
    html = _with_hero(_load("all_operational.html"), HERO_DEGRADED)
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.OK


def test_a_degraded_service_alerts_even_when_the_hero_looks_fine():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(
        RockstarStatusMonitor, "_fetch_html", return_value=_load("fivem_degraded.html")
    ):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT
    assert "FiveM" in (result.reason or "")


def test_a_page_with_a_hero_but_no_status_items_is_an_error():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    html = ('<html><body><h1 data-testid="status-hero-heading" class="x">'
            "All services operational</h1></body></html>")
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "no status items" in (result.reason or "")
    assert result.payload["hero"] == "All services operational"


def test_the_filter_error_payload_carries_the_filter_and_the_services():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(service_filter=["NoSuchThing"]))
    with patch.object(
        RockstarStatusMonitor, "_fetch_html", return_value=_load("all_operational.html")
    ):
        result = _run(monitor)
    assert result.payload["filter"] == ["NoSuchThing"]
    assert len(result.payload["services"]) == 16


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------

def test_the_healthy_payload_carries_hero_updated_at_and_services():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(
        RockstarStatusMonitor, "_fetch_html", return_value=_load("all_operational.html")
    ):
        result = _run(monitor)
    assert set(result.payload) == {"hero", "updated_at", "services"}


def test_every_service_in_the_payload_has_the_expected_keys():
    services = _extract_services(_load("all_operational.html"))
    assert all(
        {"id", "name", "section", "status_text", "status"} == set(s) for s in services
    )


def test_service_ids_are_distinct():
    ids = [s["id"] for s in _extract_services(_load("all_operational.html"))]
    assert len(set(ids)) == len(ids)


def test_updated_at_is_absent_when_the_page_omits_it():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    html = _load("all_operational.html").replace("status-hero-metadata", "removed-metadata")
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.payload["updated_at"] is None
    assert result.status == MonitorStatus.OK


def test_an_alert_renders_one_reason_item_per_degraded_service():
    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config())
    with patch.object(
        RockstarStatusMonitor, "_fetch_html", return_value=_load("fivem_degraded.html")
    ):
        result = _run(monitor)
    assert result.reason_items == ["FiveM: There are partial outages"]


# ---------------------------------------------------------------------------
# _classify — every class it can return
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("All services operational", "operational"),
        ("Scheduled maintenance", "maintenance"),
        ("Service is down", "down"),
        ("There are partial outages", "down"),
        ("There are known issues", "down"),
        ("Something else entirely", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_covers_every_class(text, expected):
    assert _classify(text) == expected


def test_classify_is_case_insensitive():
    assert _classify("ALL SERVICES OPERATIONAL") == "operational"


def test_operational_wins_over_a_later_keyword():
    """Precedence is by check order, not by position in the text."""
    assert _classify("operational after an outage") == "operational"


# ---------------------------------------------------------------------------
# _apply_filter — id, name and section
# ---------------------------------------------------------------------------

_SERVICES = [
    {"id": "cfx-re-fivem", "name": "FiveM", "section": "Cfx.re"},
    {"id": "cfx-re-redm", "name": "RedM", "section": "Cfx.re"},
    {"id": "online-services-launcher", "name": "Launcher", "section": "Online Services"},
]


def test_filter_by_canonical_id():
    assert [s["name"] for s in _apply_filter(_SERVICES, ["cfx-re-fivem"])] == ["FiveM"]


def test_filter_by_service_name():
    assert [s["name"] for s in _apply_filter(_SERVICES, ["RedM"])] == ["RedM"]


def test_filter_by_section_selects_the_whole_group():
    assert len(_apply_filter(_SERVICES, ["Cfx.re"])) == 2


def test_filter_is_case_insensitive():
    assert [s["name"] for s in _apply_filter(_SERVICES, ["fivem"])] == ["FiveM"]


def test_an_empty_filter_returns_everything():
    assert _apply_filter(_SERVICES, []) == _SERVICES


def test_several_filter_terms_are_combined():
    assert len(_apply_filter(_SERVICES, ["FiveM", "Launcher"])) == 2


def test_a_filter_matching_nothing_returns_empty():
    assert _apply_filter(_SERVICES, ["nope"]) == []


def test_the_real_fixture_can_be_filtered_by_section():
    services = _extract_services(_load("all_operational.html"))
    assert len(_apply_filter(services, ["Cfx.re"])) == 5


# ---------------------------------------------------------------------------
# Parser helpers born from real parsing artefacts
# ---------------------------------------------------------------------------

def test_strip_residue_cuts_a_trailing_partial_tag():
    """The testid lookahead can leave `<p` or `<h6` dangling at the end of a chunk."""
    assert _strip_residue("All services operational <h6") == "All services operational"


def test_strip_residue_leaves_clean_text_alone():
    assert _strip_residue("All services operational") == "All services operational"


def test_strip_residue_on_an_empty_string():
    assert _strip_residue("") == ""


def test_first_phrase_cuts_at_the_first_tag():
    assert _first_phrase("Cfx.re <span>extra</span>") == "Cfx.re"


def test_first_phrase_falls_back_when_cutting_leaves_nothing():
    assert _first_phrase("<span>only markup</span>") == "<span>only markup</span>"


def test_slugify_of_an_empty_value():
    assert _slugify("") == ""


def test_split_item_strips_a_trailing_separator_from_the_name():
    name, status = _split_item("FiveM: All services operational")
    assert name == "FiveM"
    assert status == "All services operational"
