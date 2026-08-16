"""Every bullet in the Telegram card must map to exactly one incident.

The notifier used to re-split `reason` on commas, but each module joined its parts
with a different separator and several providers put those separators inside their
own content. AWS collapsed two events into one bullet; GCP leaked a Python list repr
straight into the card.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.types import MonitorResult, MonitorStatus
from app.notifications.telegram.notifier import (
    _build_payload,
    _reason_items,
    _split_reason,
)

FIXTURES = Path(__file__).parent / "fixtures"
_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _config(slug, rule_value, service_filter=None):
    return ModuleConfig(
        slug=slug,
        url=f"https://{slug}.example.com/api/v2/summary.json",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value=rule_value),
        service_filter=service_filter or [],
        enabled=True,
    )


def _drive(monitor_cls, slug, json_body, rule_value, *, text_body=None, service_filter=None):
    monitor = monitor_cls()
    monitor.configure(_config(slug, rule_value, service_filter))
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=json_body)
    response.text = text_body or ""
    response.status_code = 200
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


def _bullets(result):
    payload = _build_payload(
        "mod", result, 60, "%Y-%m-%d", "UTC", "WARNING", "monitor_check", _NOW
    )
    return payload["reason_items"]


# ---------------------------------------------------------------------------
# AWS — semicolon-joined, used to collapse into a single bullet
# ---------------------------------------------------------------------------

def _aws_result():
    from app.modules.aws.monitor import AwsStatusMonitor

    events = json.loads((FIXTURES / "aws" / "current_events.json").read_text(encoding="utf-8"))
    return _drive(AwsStatusMonitor, "aws", events, "operational_issue")


def test_aws_three_events_render_three_bullets():
    assert len(_bullets(_aws_result())) == 3


def test_aws_bullets_carry_one_event_each():
    bullets = _bullets(_aws_result())
    assert all(";" not in b for b in bullets)
    assert any("Frankfurt" in b for b in bullets)
    assert any("Bahrain" in b for b in bullets)


def test_aws_reason_string_is_still_the_joined_sentence():
    """The webhook and the logs keep consuming `reason`; it must not change shape."""
    result = _aws_result()
    assert "; " in result.reason
    assert result.reason == "; ".join(result.reason_items)


# ---------------------------------------------------------------------------
# GCP — the Python repr leak
# ---------------------------------------------------------------------------

def _gcp_result():
    from app.modules.gcp.monitor import GcpStatusMonitor

    incidents = [
        {
            "id": "i1",
            "status_impact": "service_outage",
            "currently_affected_locations": [{"id": "us-central1"}, {"id": "us-east1"}],
            "most_recent_update": {},
        },
        {
            "id": "i2",
            "status_impact": "service_disruption",
            "currently_affected_locations": [{"id": "europe-west1"}],
            "most_recent_update": {},
        },
    ]
    return _drive(
        GcpStatusMonitor, "gcp", incidents, "service_outage,service_disruption"
    )


def test_gcp_two_incidents_render_two_bullets():
    assert len(_bullets(_gcp_result())) == 2


def test_gcp_bullets_contain_no_python_repr():
    for bullet in _bullets(_gcp_result()):
        assert "[" not in bullet
        assert "'" not in bullet


def test_gcp_multi_region_incident_lists_regions_readably():
    bullets = _bullets(_gcp_result())
    assert "us-central1, us-east1: service_outage" in bullets


def test_gcp_reason_no_longer_leaks_repr():
    assert "['" not in (_gcp_result().reason or "")


# ---------------------------------------------------------------------------
# GitHub — enrichment text containing commas
# ---------------------------------------------------------------------------

def _github_result(*, enrich):
    from app.modules.github.monitor import GitHubStatusMonitor

    summary = {
        "components": [
            {"id": "c1", "name": "API", "status": "major_outage"},
            {"id": "c2", "name": "Actions", "status": "partial_outage"},
        ]
    }
    incidents = {
        "incidents": [
            {
                "name": "Elevated errors, degraded pushes",
                "status": "investigating",
                "updated_at": "2026-08-15",
            }
        ]
    }

    def respond(url, **_):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "unresolved" in url and enrich:
            response.json = MagicMock(return_value=incidents)
        elif "summary" in url:
            response.json = MagicMock(return_value=summary)
        else:
            response.json = MagicMock(return_value={})
        return response

    monitor = GitHubStatusMonitor()
    monitor.configure(_config("github", "major_outage,partial_outage"))
    client = MagicMock()
    client.get = AsyncMock(side_effect=lambda url, **kw: respond(url, **kw))
    return asyncio.run(monitor.check(http_client=client, logger=logging.getLogger("test")))


def test_github_incident_title_with_a_comma_stays_one_bullet():
    bullets = _bullets(_github_result(enrich=True))
    assert "Incident: Elevated errors, degraded pushes (investigating, 2026-08-15)" in bullets


def test_github_enriched_alert_has_one_bullet_per_finding():
    assert len(_bullets(_github_result(enrich=True))) == 3


def test_github_without_enrichment_keeps_the_original_reason_format():
    """No enrichment must leave `reason` byte-identical to what the rule produced."""
    result = _github_result(enrich=False)
    assert result.reason == "API: major_outage, Actions: partial_outage"
    assert len(_bullets(result)) == 2


# ---------------------------------------------------------------------------
# Modules that were already correct must stay correct
# ---------------------------------------------------------------------------

def test_steam_two_degraded_services_render_two_bullets():
    from app.modules.steam.monitor import SteamMonitor

    html = (
        '<div class="service"><span class="name">Steam Store</span>'
        '<span class="status major" id="store">Offline</span></div>'
        '<div class="service"><span class="name">Steam Community</span>'
        '<span class="status minor" id="community">Slow</span></div>'
    )
    monitor = SteamMonitor()
    monitor.configure(_config("steam", "major,minor"))
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        SteamMonitor, "_fetch_html", return_value=html
    ):
        result = asyncio.run(
            monitor.check(http_client=None, logger=logging.getLogger("test"))
        )
    assert len(_bullets(result)) == 2


def test_oci_two_incidents_render_two_bullets():
    from app.modules.oci.monitor import OciStatusMonitor

    feed = (FIXTURES / "oci" / "incident_summary.rss").read_text(encoding="utf-8")
    result = _drive(OciStatusMonitor, "oci", None, "resolved", text_body=feed)
    assert result.status == MonitorStatus.ALERT
    assert len(_bullets(result)) == len(result.payload)


def test_single_incident_renders_one_bullet():
    from app.modules.github.monitor import GitHubStatusMonitor

    summary = {"components": [{"id": "c1", "name": "API", "status": "major_outage"}]}
    result = _drive(GitHubStatusMonitor, "github", summary, "major_outage")
    assert _bullets(result) == ["API: major_outage"]


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

def test_reason_items_prefers_the_structured_list():
    result = MonitorResult(
        MonitorStatus.ALERT, "m", "a, b", 1.0, None, reason_items=["one, with comma", "two"]
    )
    assert _reason_items(result, result.reason) == ["one, with comma", "two"]


def test_reason_items_falls_back_to_splitting_when_absent():
    result = MonitorResult(MonitorStatus.ALERT, "m", "a, b", 1.0, None)
    assert _reason_items(result, result.reason) == ["a", "b"]


def test_reason_items_drops_blank_entries():
    result = MonitorResult(
        MonitorStatus.ALERT, "m", "x", 1.0, None, reason_items=["a", "", "  ", "b"]
    )
    assert _reason_items(result, "x") == ["a", "b"]


def test_reason_items_strips_whitespace():
    result = MonitorResult(MonitorStatus.ALERT, "m", "x", 1.0, None, reason_items=["  a  "])
    assert _reason_items(result, "x") == ["a"]


def test_empty_list_falls_back_rather_than_rendering_nothing():
    result = MonitorResult(MonitorStatus.ALERT, "m", "a, b", 1.0, None, reason_items=[])
    assert _reason_items(result, result.reason) == ["a", "b"]


def test_split_reason_still_works_for_legacy_callers():
    assert _split_reason("a, b, c") == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Recovery cards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_recovery_bullet_is_not_split_on_a_name_comma():
    from app.core.notifications import _build_service_result

    base = MonitorResult(MonitorStatus.OK, "ok", None, 1.0, None)
    item = {"id": "c1", "name": "Actions, Packages", "status": "operational"}
    recovery = _build_service_result(base, item, MonitorStatus.OK, "service restored")
    assert recovery.reason_items == ["Actions, Packages: operational"]
    assert len(_bullets(recovery)) == 1
