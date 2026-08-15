"""Tests for the Steam monitor."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorStatus
from app.modules.steam.monitor import (
    SteamMonitor,
    _parse_services,
    get_monitor,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "steam"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _make_config(
    service_filter: list[str] | None = None,
    rule_kind: str = "status",
    rule_value: str = "major,minor",
    timeout: float = 10.0,
) -> ModuleConfig:
    return ModuleConfig(
        slug="steam",
        url="https://steamstat.us/",
        interval_seconds=60,
        timeout_seconds=timeout,
        user_agent="test/1.0",
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _run(monitor: SteamMonitor):
    import asyncio

    return asyncio.run(monitor.check(http_client=None, logger=logging.getLogger("test")))


def _degrade(html: str, service_id: str, severity: str, text: str) -> str:
    """Flip one service's status class and text in the fixture."""
    marker = f'id="{service_id}"'
    idx = html.find(marker)
    assert idx > 0, f"{service_id} not in fixture"
    start = html.rfind('<span class="status ', 0, idx)
    end = html.find("</span>", idx)
    assert start > 0 and end > start
    return html[:start] + f'<span class="status {severity}" {marker}>{text}</span>' + html[end + len("</span>"):]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_services_from_real_fixture():
    services = list(_parse_services(_load("all_operational.html")))
    assert len(services) == 15
    by_id = {s["id"]: s for s in services}
    assert "store" in by_id
    assert by_id["store"]["severity"] == "good"
    assert all(s["severity"] is not None for s in services)


def test_parse_services_ignores_pageviews():
    services = list(_parse_services(_load("all_operational.html")))
    assert "pageviews" not in {s["id"] for s in services}


def test_parse_services_strips_inner_markup():
    body = (
        '<div class="service"><span class="name"><a href="#">Steam <b>Store</b></a></span>'
        '<span class="status good" id="store">Normal</span></div>'
    )
    services = list(_parse_services(body))
    assert services[0]["name"] == "Steam Store"


# ---------------------------------------------------------------------------
# check() — happy paths
# ---------------------------------------------------------------------------

def test_check_healthy_returns_ok():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.OK
    assert result.message == "steam reachable"
    assert len(result.payload) == 15
    assert result.duration_ms is not None


def test_check_alert_when_service_degraded():
    html = _degrade(_load("all_operational.html"), "store", "major", "Offline")
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(SteamMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT
    assert "Offline" in (result.reason or "")
    assert [s["id"] for s in result.payload] == ["store"]


def test_check_alert_respects_service_filter():
    html = _degrade(_load("all_operational.html"), "store", "major", "Offline")
    html = _degrade(html, "community", "minor", "Slow")
    monitor = SteamMonitor()
    monitor.configure(_make_config(service_filter=["community"]))
    with patch.object(SteamMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT
    assert [s["id"] for s in result.payload] == ["community"]


def test_check_filtered_service_healthy_returns_ok():
    html = _degrade(_load("all_operational.html"), "store", "major", "Offline")
    monitor = SteamMonitor()
    monitor.configure(_make_config(service_filter=["community"]))
    with patch.object(SteamMonitor, "_fetch_html", return_value=html):
        result = _run(monitor)
    assert result.status == MonitorStatus.OK


def test_check_filter_unmatched_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config(service_filter=["nonexistent"]))
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "filter" in (result.reason or "").lower()


# ---------------------------------------------------------------------------
# check() — failure paths (this is the bug the module shipped with)
# ---------------------------------------------------------------------------

def test_check_http_403_returns_error_not_exception():
    """Cloudflare refusing the TLS fingerprint must degrade, not crash the scheduler."""
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(
        SteamMonitor, "_fetch_html", side_effect=RuntimeError("upstream returned HTTP 403")
    ):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "403" in (result.reason or "")
    assert result.duration_ms is not None


def test_check_empty_body_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(
        SteamMonitor, "_fetch_html", side_effect=RuntimeError("upstream returned empty body")
    ):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "empty body" in (result.reason or "")


def test_check_network_failure_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(SteamMonitor, "_fetch_html", side_effect=OSError("boom")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "boom" in (result.reason or "")
    assert "OSError" in (result.reason or "")
    assert result.duration_ms is not None


def test_check_unparseable_body_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    with patch.object(SteamMonitor, "_fetch_html", return_value="<html><body>nope</body></html>"):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "no services" in (result.reason or "").lower()


# ---------------------------------------------------------------------------
# Rule kinds
# ---------------------------------------------------------------------------

def test_check_keyword_rule_match():
    monitor = SteamMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="Steam Store"))
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ALERT


def test_check_keyword_rule_no_match():
    monitor = SteamMonitor()
    monitor.configure(_make_config(rule_kind="keyword", rule_value="zzz-not-present"))
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.OK


def test_check_regex_rule_invalid_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config(rule_kind="regex", rule_value="["))
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "invalid regex" in (result.reason or "")


def test_check_unsupported_rule_kind_returns_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config(rule_kind="telepathy", rule_value="x"))
    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = _run(monitor)
    assert result.status == MonitorStatus.ERROR
    assert "unsupported rule kind" in (result.reason or "")


# ---------------------------------------------------------------------------
# Transport wiring
# ---------------------------------------------------------------------------

def test_impersonate_profile_defaults_to_current():
    assert SteamMonitor()._impersonate == "chrome124"


def test_impersonate_profile_reads_env(monkeypatch):
    monkeypatch.setenv("STEAM_IMPERSONATE_PROFILE", "safari17_0")
    assert SteamMonitor()._impersonate == "safari17_0"


def test_fetch_html_uses_impersonation_and_configured_timeout():
    monitor = SteamMonitor()
    monitor.configure(_make_config(timeout=7.5))
    fake = type("R", (), {"status_code": 200, "text": "<html>ok</html>"})()
    with patch("curl_cffi.requests.get", return_value=fake) as mocked:
        assert monitor._fetch_html() == "<html>ok</html>"
    _, kwargs = mocked.call_args
    assert kwargs["impersonate"] == "chrome124"
    assert kwargs["timeout"] == 7.5


def test_fetch_html_raises_on_http_error():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    fake = type("R", (), {"status_code": 403, "text": "denied"})()
    with patch("curl_cffi.requests.get", return_value=fake):
        with pytest.raises(RuntimeError, match="HTTP 403"):
            monitor._fetch_html()


def test_fetch_html_raises_on_empty_body():
    monitor = SteamMonitor()
    monitor.configure(_make_config())
    fake = type("R", (), {"status_code": 200, "text": ""})()
    with patch("curl_cffi.requests.get", return_value=fake):
        with pytest.raises(RuntimeError, match="empty body"):
            monitor._fetch_html()


def test_check_does_not_use_the_shared_httpx_client():
    """The scheduler still passes one; steam must ignore it after this change."""
    monitor = SteamMonitor()
    monitor.configure(_make_config())

    class Boom:
        async def get(self, *a, **k):
            raise AssertionError("steam must not use the shared httpx client")

    import asyncio

    with patch.object(SteamMonitor, "_fetch_html", return_value=_load("all_operational.html")):
        result = asyncio.run(monitor.check(http_client=Boom(), logger=logging.getLogger("test")))
    assert result.status == MonitorStatus.OK


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_get_monitor_returns_configured_slug():
    m = get_monitor("steam")
    assert isinstance(m, SteamMonitor)
    assert m.id == "steam"


def test_check_unconfigured_raises():
    monitor = SteamMonitor()
    with pytest.raises(RuntimeError):
        _run(monitor)
