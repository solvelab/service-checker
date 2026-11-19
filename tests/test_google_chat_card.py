"""A card that says what broke, when, and for how long (#142).

The card used to read "Service alert / Module: openai / Message: service degraded" with a UTC
ISO timestamp: nobody could tell which part of the provider was broken, the clock was three
hours off for the team, and a recovery never said how long the incident lasted. These tests
pin the readable card, the alert start that feeds "ongoing for" and "lasted", the start
surviving a restart, and the page incidents that name the problem.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import (
    GoogleChatConfig,
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
    drain_config_warnings,
    load_app_config,
)
from app.core.notifications import NotificationManager
from app.core.statuspage import attach_incidents, attach_open_incidents
from app.core.types import MonitorResult, MonitorStatus
from app.notifications.google_chat.notifier import GoogleChatNotifier
from app.notifications.webhook.notifier import WebhookNotifier

#: Real clock: StateStore.load() ages entries against it (see test_state_persistence).
_T0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
_START = datetime(2026, 11, 19, 16, 1, tzinfo=timezone.utc)  # 13:01 in Sao Paulo
_LUNA = "Elevated error rates for gpt-6 Luna API requests"


def _chat_config(zone="America/Sao_Paulo"):
    return GoogleChatConfig(True, "https://chat.googleapis.com/v1/spaces/X/messages?key=k&token=t",
                            0.0, True, zone)


def _item(**extra):
    return {"id": "resp", "name": "Responses", "status": "partial_outage", **extra}


def _result(item=None, started_at=None, page_url="https://status.openai.com",
            status=MonitorStatus.ALERT):
    return MonitorResult(status=status, message="service degraded", reason="Responses",
                         duration_ms=1.0, payload=[item or _item()], started_at=started_at,
                         page_url=page_url)


def _card(method, result, event_time, zone="America/Sao_Paulo", module_id="openai"):
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200, text="{}"))
    asyncio.run(getattr(GoogleChatNotifier(_chat_config(zone)), method)(
        module_id=module_id, result=result, interval_seconds=60, level_name="WARNING",
        event_name="monitor_check", event_time=event_time, http_client=client,
        logger=MagicMock(spec=logging.Logger)))
    return client.post.call_args.kwargs["json"]["cardsV2"][0]["card"]


def _fields(card):
    out = {}
    for widget in card["sections"][0]["widgets"]:
        if "decoratedText" in widget:
            out.setdefault(widget["decoratedText"]["topLabel"], []).append(
                widget["decoratedText"]["text"])
        else:
            out.setdefault("_text", []).append(widget["textParagraph"]["text"])
    return out


@pytest.fixture(autouse=True)
def _forget_404s():
    """The 404 memo is process-wide: no test may inherit another's."""
    from app.core import statuspage
    statuspage._NO_UNRESOLVED.clear()
    yield
    statuspage._NO_UNRESOLVED.clear()


# -- the card ---------------------------------------------------------------------------


def test_the_title_names_provider_component_and_state():
    card = _card("send_alert", _result(started_at=_START), _START)
    assert card["header"]["title"] == "🚨 OpenAI · Responses — Partial outage"


def test_a_first_alert_says_when_in_local_time_without_ongoing():
    fields = _fields(_card("send_alert", _result(started_at=_START), _START))
    assert fields["When"] == ["19/11 13:01 (UTC-3)"]
    assert "Ongoing" not in fields


def test_a_repeat_says_how_long_it_has_been_ongoing():
    fields = _fields(_card("send_alert", _result(started_at=_START), _START + timedelta(minutes=25)))
    assert fields["Ongoing"] == ["for 25m (since 13:01)"]


def test_a_repeat_whose_state_moved_says_from_and_to():
    item = _item(status="major_outage", from_status="degraded_performance")
    fields = _fields(_card("send_alert", _result(item, _START), _START + timedelta(minutes=10)))
    assert fields["State changed"] == ["Degraded performance → Major outage"]


def test_a_recovery_says_how_long_it_lasted():
    card = _card("send_recovery", _result(started_at=_START, status=MonitorStatus.OK),
                 _START + timedelta(minutes=57))
    assert card["header"]["title"] == "✅ OpenAI · Responses — Recovered"
    assert _fields(card)["Lasted"] == ["57m (13:01 → 13:58)"]


def test_a_recovery_without_a_known_start_omits_the_duration():
    card = _card("send_recovery", _result(status=MonitorStatus.OK), _START)
    assert "Lasted" not in _fields(card)


def test_page_incidents_replace_the_generic_message():
    fields = _fields(_card("send_alert", _result(_item(incidents=[_LUNA]), _START), _START))
    assert fields["Status page incident"] == [_LUNA]
    assert not any("service degraded" in t for v in fields.values() for t in v)


def test_the_status_page_is_linked_and_escaped():
    fields = _fields(_card("send_alert", _result(page_url='https://x.io/"><b>'), _START))
    link = fields["_text"][-1]
    assert link.startswith('<a href="https://x.io/&quot;&gt;&lt;b&gt;">')
    assert "<b>" not in link.replace("<a href", "").replace("</a>", "")


def test_incident_titles_are_escaped():
    fields = _fields(_card("send_alert", _result(_item(incidents=["<i>x</i>"]), _START), _START))
    assert fields["Status page incident"] == ["&lt;i&gt;x&lt;/i&gt;"]


@pytest.mark.parametrize("zone, expected", [("UTC", "19/11 16:01 (UTC)"),
                                            ("Asia/Kolkata", "19/11 21:31 (UTC+5:30)")])
def test_the_zone_is_configurable(zone, expected):
    assert _fields(_card("send_alert", _result(), _START, zone=zone))["When"] == [expected]


def test_an_unknown_module_keeps_its_slug():
    card = _card("send_alert", _result(), _START, module_id="acme")
    assert card["header"]["title"].startswith("🚨 acme · Responses")


def test_timezone_env_and_invalid_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_CHAT_TIMEZONE", "UTC")
    assert load_app_config().notifications.google_chat.timezone == "UTC"
    monkeypatch.setenv("GOOGLE_CHAT_TIMEZONE", "Mars/Olympus")
    assert load_app_config().notifications.google_chat.timezone == "America/Sao_Paulo"
    drain_config_warnings()


# -- the alert start --------------------------------------------------------------------


class Spy:
    def __init__(self):
        self.alerts: list = []
        self.recoveries: list = []

    async def send_alert(self, **kwargs):
        self.alerts.append(kwargs["result"])
        return True

    async def send_recovery(self, **kwargs):
        self.recoveries.append(kwargs["result"])
        return True

    async def send_monitor_error(self, **kwargs):
        return True

    async def send_monitor_recovered(self, **kwargs):
        return True


def _manager(path=None):
    manager = NotificationManager(NotificationConfig(
        telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10, recovery_threshold=1,
        state_path=str(path) if path else None))
    spy = Spy()
    manager.register("spy", spy)
    return manager, spy


async def _feed(manager, status, payload, minute, module_id="openai"):
    await manager.handle_result(
        module_id=module_id,
        result=MonitorResult(status, "m", "degraded" if status == MonitorStatus.ALERT else None,
                             10.0, payload),
        module_config=ModuleConfig("openai", "https://status.openai.com/api/v2/summary.json", 60,
                                   10.0, "ua", RuleConfig("status", "partial_outage"), [], True),
        level_name="WARNING", event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=AsyncMock(), logger=MagicMock(spec=logging.Logger))


@pytest.mark.asyncio
async def test_the_start_is_kept_through_repeats_and_reaches_the_recovery():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_item()], 0)
    await _feed(manager, MonitorStatus.ALERT, [_item()], 11)
    await _feed(manager, MonitorStatus.ALERT, [_item()], 22)
    await _feed(manager, MonitorStatus.OK, [], 30)
    assert [r.started_at for r in spy.alerts] == [_T0] * 3
    assert spy.recoveries[0].started_at == _T0
    assert spy.alerts[0].page_url == "https://status.openai.com"


@pytest.mark.asyncio
async def test_a_state_change_repeat_carries_the_previous_state():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_item(status="degraded_performance")], 0)
    await _feed(manager, MonitorStatus.ALERT, [_item(status="major_outage")], 1)  # throttled
    await _feed(manager, MonitorStatus.ALERT, [_item(status="major_outage")], 11)
    assert len(spy.alerts) == 2
    assert spy.alerts[-1].payload[0]["from_status"] == "degraded_performance"


@pytest.mark.asyncio
async def test_the_start_survives_a_restart(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_item()], 0)
    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 40)
    assert spy.recoveries[0].started_at == _T0


@pytest.mark.asyncio
async def test_a_legacy_state_without_a_start_counts_from_its_last_alert(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_item()], 0)
    data = json.loads(path.read_text())
    for entry in data["alerts"].values():
        entry.pop("opened_at", None)
    path.write_text(json.dumps(data))
    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 40)
    assert spy.recoveries[0].started_at == _T0


@pytest.mark.asyncio
async def test_a_start_from_the_future_is_dropped(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_item()], 0)
    data = json.loads(path.read_text())
    for entry in data["alerts"].values():
        entry["opened_at"] = (_T0 + timedelta(days=1)).isoformat()
    path.write_text(json.dumps(data))
    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 40)
    assert spy.recoveries[0].started_at == _T0  # the last alert, not tomorrow


# -- webhook ----------------------------------------------------------------------------


def _webhook(method, result, event_time):
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200, text="{}"))
    notifier = WebhookNotifier(WebhookConfig(True, "https://hook.example", None, "Authorization"))
    asyncio.run(getattr(notifier, method)(
        module_id="openai", result=result, interval_seconds=60, level_name="INFO",
        event_name="e", event_time=event_time, http_client=client,
        logger=MagicMock(spec=logging.Logger)))
    return client.post.call_args.kwargs["json"]


def test_the_webhook_recovery_carries_start_and_duration():
    body = _webhook("send_recovery", _result(started_at=_START), _START + timedelta(minutes=57))
    assert body["started_at"] == _START.isoformat() and body["duration_seconds"] == 57 * 60


def test_the_webhook_without_a_start_sends_nulls():
    body = _webhook("send_recovery", _result(), _START)
    assert body["started_at"] is None and body["duration_seconds"] is None


# -- page incidents ---------------------------------------------------------------------


def test_an_incident_goes_only_on_the_components_it_lists():
    payload = [{"name": "Responses"}, {"name": "Codex"}]
    attach_incidents(payload, [{"name": "A", "status": "investigating",
                                "components": [{"name": "codex"}]}])
    assert "incidents" not in payload[0] and payload[1]["incidents"] == ["A"]


def test_an_incident_without_components_goes_on_every_item_as_page_wide():
    payload = [{"name": "Responses"}, {"name": "Codex"}]
    attach_incidents(payload, [{"name": _LUNA, "status": "investigating", "components": []}])
    assert payload[0]["incidents"] == payload[1]["incidents"] == [_LUNA]
    assert payload[0]["page_incidents"] == payload[1]["page_incidents"] == [_LUNA]


def test_closed_and_malformed_incidents_are_skipped_and_capped():
    payload = [{"name": "Responses"}]
    attach_incidents(payload, [{"name": "old", "status": "resolved"}, "junk", {"status": "x"}]
                     + [{"name": f"n{i}", "status": "identified"} for i in range(5)])
    assert payload[0]["incidents"] == payload[0]["page_incidents"] == ["n0", "n1", "n2"]


def _http(*responses):
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


def _response(status, body=None):
    return httpx.Response(status, json=body or {},
                          request=httpx.Request("GET", "https://status.openai.com/x"))


_CFG = ModuleConfig("openai", "https://status.openai.com/api/v2/summary.json", 60, 10.0, "ua",
                    RuleConfig("status", "x"), [], True)


@pytest.mark.asyncio
async def test_a_404_on_unresolved_falls_back_to_the_full_list():
    payload = [{"name": "Responses"}]
    client = _http(_response(404), _response(200, {"incidents": [
        {"name": _LUNA, "status": "investigating"}, {"name": "old", "status": "resolved"}]}))
    await attach_open_incidents(payload, client, _CFG, MagicMock(spec=logging.Logger))
    assert payload[0]["incidents"] == payload[0]["page_incidents"] == [_LUNA]
    urls = [c.args[0] for c in client.get.call_args_list]
    assert urls == ["https://status.openai.com/api/v2/incidents/unresolved.json",
                    "https://status.openai.com/api/v2/incidents.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [httpx.ConnectTimeout("slow"), _response(500)])
async def test_a_failed_lookup_leaves_the_items_and_logs(outcome):
    payload = [{"name": "Responses"}]
    logger = MagicMock(spec=logging.Logger)
    await attach_open_incidents(payload, _http(outcome), _CFG, logger)
    assert payload == [{"name": "Responses"}]
    assert logger.debug.call_args.kwargs["extra"]["event"] == "incident_lookup_failed"


# -- bug-hunter findings ----------------------------------------------------------------


def test_the_monitors_own_detail_stays_on_the_card():
    result = MonitorResult(MonitorStatus.ALERT, "m", "us-east1", 1.0,
                           [{"id": "abc", "status": "SERVICE_OUTAGE"}],
                           reason_items=["Cloud Run: SERVICE_OUTAGE (us-east1)"])
    texts = _fields(_card("send_alert", result, _START, module_id="gcp"))["_text"]
    assert "• Cloud Run: SERVICE_OUTAGE (us-east1)" in texts


def test_status_text_is_the_title_state():
    item = {"id": "page", "name": "ElevenLabs", "status": "minor",
            "status_text": "Partially degraded service"}
    card = _card("send_alert", _result(item), _START, module_id="elevenlabs")
    assert card["header"]["title"].endswith("— Partially degraded service")


def test_a_numeric_status_is_not_the_title_state():
    item = {"id": "ec2", "name": "Amazon EC2", "status": "3"}
    card = _card("send_alert", _result(item), _START, module_id="aws")
    assert card["header"]["title"] == "🚨 AWS · Amazon EC2 — Service alert"


@pytest.mark.parametrize("at, expected", [
    (datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc), "19/11 11:30 (UTC-3:30)"),
    (datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc), "19/07 12:30 (UTC-2:30)"),
])
def test_a_negative_half_hour_zone_is_spelled_right(at, expected):
    fields = _fields(_card("send_alert", _result(), at, zone="America/St_Johns"))
    assert fields["When"] == [expected]


def test_a_start_after_the_event_never_yields_a_negative_duration():
    late = _result(started_at=_START + timedelta(minutes=5), status=MonitorStatus.OK)
    assert "Lasted" not in _fields(_card("send_recovery", late, _START))
    assert _webhook("send_recovery", late, _START)["duration_seconds"] == 0


@pytest.mark.asyncio
async def test_a_vanished_recovery_does_not_quote_old_incidents(tmp_path):
    manager, spy = _manager(tmp_path / "state.json")
    a = _item(id="a", name="Responses", incidents=[_LUNA])
    b = _item(id="b", name="Codex", incidents=[_LUNA])
    await _feed(manager, MonitorStatus.ALERT, [a, b], 0)
    await _feed(manager, MonitorStatus.ALERT, [_item(id="a", name="Responses")], 1)
    assert len(spy.recoveries) == 1
    assert "incidents" not in spy.recoveries[0].payload[0]
    assert "incidents" not in (tmp_path / "state.json").read_text()


def test_a_non_list_components_field_never_raises():
    payload = [{"name": "Responses"}]
    attach_incidents(payload, [{"name": "X", "status": "investigating", "components": 5},
                               {"name": "Y", "status": "investigating", "components": "api"}])
    assert payload[0]["incidents"] == payload[0]["page_incidents"] == ["X", "Y"]


@pytest.mark.asyncio
async def test_the_404_is_remembered_so_an_outage_costs_one_lookup_per_cycle():
    from app.core import statuspage
    statuspage._NO_UNRESOLVED.discard("https://status.openai.com")
    body = {"incidents": [{"name": _LUNA, "status": "investigating"}]}
    client = _http(_response(404), _response(200, body), _response(200, body))
    for _ in range(2):
        await attach_open_incidents([{"name": "Responses"}], client, _CFG,
                                    MagicMock(spec=logging.Logger))
    assert client.get.call_count == 3
    statuspage._NO_UNRESOLVED.discard("https://status.openai.com")
