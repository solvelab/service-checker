"""Every alert names the impacted service (#146).

Checked against the real feeds on 2026-11-19: GCP items carried no name, so the card said
only "Google Cloud — Service outage"; the ElevenLabs indicator card said only "ElevenLabs —
Minor"; and an incident.io incident that lists no component was shown on every alerting
component as if it were that component's. The GCP fixture is two real incidents from
status.cloud.google.com, reopened (`end` cleared).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.types import MonitorResult, MonitorStatus
from app.modules.gcp.monitor import _product_name
from tests.test_gcp_monitor import _config, _run
from tests.test_google_chat_card import _card, _fields, _manager, _feed, _item

FIXTURE = Path(__file__).parent / "fixtures" / "gcp" / "incidents_products.json"
_AT = datetime(2026, 11, 19, 16, 1, tzinfo=timezone.utc)


def _real():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# -- GCP --------------------------------------------------------------------------------


def test_a_single_product_incident_is_named_by_its_service():
    result = _run(_real())
    assert result.payload[0]["name"] == "Google Cloud Storage (us-central1)"
    assert result.reason_items[0] == "Google Cloud Storage (us-central1): service_disruption"


def test_multiple_products_lists_three_then_counts_the_rest():
    multi = _real()[1]
    titles = [p["title"] for p in multi["affected_products"]]
    assert _product_name(multi) == f"{', '.join(titles[:3])} +{len(titles) - 3}"


@pytest.mark.parametrize("incident, expected", [
    ({"service_name": "Multiple Products", "affected_products": [{"title": "Apigee"}]}, "Apigee"),
    ({"service_name": "Multiple Products"}, "Google Cloud incident"),
    ({"service_name": "multiple products", "affected_products": []}, "Google Cloud incident"),
    ({"affected_products": [{"title": "A"}, {"title": "A"}, {"title": "B"}]}, "A, B"),
    ({"service_name": "x" * 200}, "x" * 79 + "…"),
    ({"service_name": " ", "affected_products": "junk"}, "Google Cloud incident"),
    ({"affected_products": [{"title": 5}, {}, {"title": "A"}, {"title": "B"}]}, "A, B"),
])
def test_the_name_never_breaks_on_odd_fields(incident, expected):
    assert _product_name(incident) == expected


def test_the_gcp_card_names_the_product():
    item = _run(_real()).payload[0]
    result = MonitorResult(MonitorStatus.ALERT, "m", "r", 1.0, [item])
    card = _card("send_alert", result, _AT, module_id="gcp")
    assert card["header"]["title"].startswith("🚨 Google Cloud · Google Cloud Storage (us-central1) — ")


def test_naming_the_item_keeps_the_alert_key_so_a_deploy_does_not_re_alert():
    manager, spy = _manager()
    item = _run(_real()).payload[0]
    unnamed = {k: v for k, v in item.items() if k != "name"}
    asyncio.run(_feed(manager, MonitorStatus.ALERT, [unnamed], 0))   # before #146
    asyncio.run(_feed(manager, MonitorStatus.ALERT, [item], 1))      # after the deploy
    assert len(spy.alerts) == 1 and spy.recoveries == []


def test_the_region_filter_still_applies():
    assert _run(_real(), _config(service_filter=["asia-east1"])).status == MonitorStatus.OK


# -- ElevenLabs indicator ---------------------------------------------------------------


def test_the_indicator_card_does_not_repeat_the_provider_and_cites_the_incident():
    page = {"id": "page", "name": "ElevenLabs", "status": "minor",
            "status_text": "Partially degraded service",
            "incidents": ["Degraded performance of Voice Library voices [EU]"]}
    card = _card("send_alert", MonitorResult(MonitorStatus.ALERT, "m", "r", 1.0, [page]), _AT,
                 module_id="elevenlabs")
    assert card["header"]["title"] == "🚨 ElevenLabs — Partially degraded service"
    assert _fields(card)["Status page incident"] == [
        "Degraded performance of Voice Library voices [EU]"]


# -- page-wide incidents ----------------------------------------------------------------


def test_a_page_wide_incident_has_its_own_label():
    item = _item(incidents=["Codex down", "Elevated error rates"],
                 page_incidents=["Elevated error rates"])
    card = _card("send_alert", MonitorResult(MonitorStatus.ALERT, "m", "r", 1.0, [item]), _AT)
    fields = _fields(card)
    assert fields["Status page incident"] == ["Codex down"]
    assert fields["Open page incident (no component listed)"] == ["Elevated error rates"]


@pytest.mark.asyncio
async def test_page_incidents_never_reach_the_stored_state(tmp_path):
    manager, spy = _manager(tmp_path / "state.json")
    await _feed(manager, MonitorStatus.ALERT, [_item(page_incidents=["X"])], 0)
    assert "page_incidents" not in (tmp_path / "state.json").read_text()


# -- bug-hunter findings ----------------------------------------------------------------


def _gcp(incident_id, region, service="Google Cloud Storage"):
    return {"id": incident_id, "service_name": service, "status_impact": "SERVICE_OUTAGE",
            "end": None, "currently_affected_locations": [{"id": region, "title": region}]}


def test_the_delivered_gcp_alert_keeps_product_and_region():
    manager, spy = _manager()
    payload = _run(_real()).payload
    asyncio.run(_feed(manager, MonitorStatus.ALERT, payload, 0, module_id="gcp"))
    assert spy.alerts[0].reason_items == ["Google Cloud Storage (us-central1): service_disruption"]
    card = _card("send_alert", spy.alerts[0], _AT, module_id="gcp")
    assert any("us-central1" in line for line in _fields(card)["_text"])


def test_two_incidents_of_one_product_read_differently():
    payload = _run([_gcp("a", "us-central1"), _gcp("b", "us-east1")]).payload
    assert payload[0]["name"] != payload[1]["name"]


def test_id_less_incidents_keep_keys_of_their_own():
    a, b = _gcp(None, "us-central1"), _gcp(None, "us-east1")
    a["number"], b["number"] = "111", "222"
    manager, spy = _manager()
    asyncio.run(_feed(manager, MonitorStatus.ALERT, _run([a, b]).payload, 0, module_id="gcp"))
    asyncio.run(_feed(manager, MonitorStatus.ALERT, _run([a]).payload, 1, module_id="gcp"))
    assert len(spy.alerts) == 2 and len(spy.recoveries) == 1
