"""Tests for the Rockstar Services monitor."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.rockstar.monitor import (
    RockstarStatusMonitor,
    _classify,
    _extract_services,
    _slugify,
    _split_item,
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
    html = _load("all_operational.html").replace(
        ">FiveM</h6>", ">FiveM</h6>"
    )
    # Inject a degraded status into the FiveM item by replacing its status text.
    # The fixture has multiple "All services operational" — we target the FiveM one.
    # Strategy: replace the first occurrence right after "FiveM" with a degraded phrase.
    idx = html.find("FiveM")
    assert idx > 0
    head, tail = html[:idx], html[idx:]
    tail_modified = tail.replace("All services operational", "There are partial outages", 1)
    degraded_html = head + tail_modified

    monitor = RockstarStatusMonitor()
    monitor.configure(_make_config(service_filter=["FiveM"]))
    with patch.object(RockstarStatusMonitor, "_fetch_html", return_value=degraded_html):
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
