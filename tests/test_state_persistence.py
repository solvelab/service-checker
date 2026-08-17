"""Tests for alert state that survives a restart.

The gap this closes was found by failing to prove something. During the production
rollout an alert was forced on purpose, and it fired on both channels. Reverting the
configuration restarted the pod — and with it went `_alert_state`, so the recovery could
never be demonstrated end to end. No provider announces twice: if the degradation ends
while the process is down, the payload comes back healthy, the state is empty, and there
is no transition left to notify.

That is the same failure mode the vanished-component reconciliation fixed, arriving by a
different road. So the decisive test here mirrors that one: alert, throw the manager
away, build a new one from the file, feed the healthy payload, and demand the all-clear.

The store is written to never break the monitor, so most of this file is about what
happens when the file is wrong: corrupt, truncated, from another version, unwritable.
Every one of those must degrade to "start empty and keep monitoring".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import AlertState, MonitorErrorState, NotificationManager
from app.core.state_store import SCHEMA_VERSION, StateStore
from app.core.types import MonitorResult, MonitorStatus

#: Ancora de tempo da suite, presa ao **instante atual** e nao a uma data fixa.
#:
#: `StateStore.load()` descarta o que passou de `NOTIFICATION_STATE_MAX_AGE_MINUTES`
#: comparando com o relogio real, porque o `NotificationManager` nao injeta `now`. Com
#: uma data fixa, o estado que estes testes gravam envelhecia sozinho: a suite passava
#: no dia em que foi escrita e quebrava 24 horas depois, sem ninguem ter tocado em nada.
#: Cinco testes cairam assim, e o CI verde do merge nao tinha como pegar.
#:
#: `test_the_anchor_cannot_age_out` guarda esta escolha.
_T0 = datetime.now(timezone.utc).replace(microsecond=0)


class Spy:
    def __init__(self):
        self.alerts: list[str] = []
        self.recoveries: list[str] = []

    async def send_alert(self, **kwargs):
        self.alerts.append(kwargs["result"].reason or "")

    async def send_recovery(self, **kwargs):
        self.recoveries.append(kwargs["result"].reason or "")

    async def send_monitor_error(self, **kwargs): ...

    async def send_monitor_recovered(self, **kwargs): ...


def _config(state_path=None, max_age=1440, threshold=3):
    return NotificationConfig(
        telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
        webhook=WebhookConfig(False, None, None, "Authorization"),
        repeat_minutes=10,
        error_threshold=threshold,
        state_path=str(state_path) if state_path else None,
        state_max_age_minutes=max_age,
    )


def _module_config(slug="aws"):
    return ModuleConfig(
        slug=slug,
        url="https://example.com",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value="open"),
        service_filter=[],
        enabled=True,
    )


def _manager(state_path=None, **kwargs):
    """A fresh manager — the test's stand-in for a fresh process."""
    manager = NotificationManager(_config(state_path, **kwargs))
    spy = Spy()
    manager.register("spy", spy)
    return manager, spy


async def _feed(manager, status, payload, minute, module_id="aws"):
    await manager.handle_result(
        module_id=module_id,
        result=MonitorResult(
            status, "m", "degraded" if status == MonitorStatus.ALERT else None, 10.0, payload
        ),
        module_config=_module_config(module_id),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=AsyncMock(),
        logger=MagicMock(spec=logging.Logger),
    )


def _c(cid, name, status="open"):
    return {"id": cid, "name": name, "status": status}


# ---------------------------------------------------------------------------
# A ancora de tempo
# ---------------------------------------------------------------------------

def test_the_anchor_cannot_age_out():
    """Uma data fixa aqui faz a suite quebrar sozinha 24 horas depois de escrita.

    Foi o que aconteceu: cinco testes passaram a falhar sem que nenhuma linha de codigo
    mudasse, porque o estado que eles gravavam excedia o limite de idade quando lido com
    o relogio real. O CI verde do merge nao tem como pegar isso — so a terceira execucao,
    no dia seguinte.
    """
    from app.core.config import NotificationConfig

    default_max_age = NotificationConfig.__dataclass_fields__["state_max_age_minutes"].default
    idade = (datetime.now(timezone.utc) - _T0).total_seconds() / 60
    assert idade < default_max_age / 2, (
        f"a ancora tem {idade:.0f} min e o limite default e {default_max_age}; "
        "prenda-a ao instante atual em vez de a uma data fixa"
    )


# ---------------------------------------------------------------------------
# The defect: an incident that spans a restart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_all_clear_survives_a_restart(tmp_path):
    """The decisive one. Two managers, one file, one all-clear."""
    path = tmp_path / "state.json"

    before, spy_before = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_c("dx-eu", "Direct Connect")], 0)
    assert len(spy_before.alerts) == 1

    after, spy_after = _manager(path)          # the restart
    await _feed(after, MonitorStatus.OK, [], 5)

    assert len(spy_after.recoveries) == 1
    assert "Direct Connect" in spy_after.recoveries[0]


@pytest.mark.asyncio
async def test_without_a_path_the_all_clear_is_lost_exactly_as_before(tmp_path):
    """Pins the old behaviour, so the fix is visibly the fix and not a coincidence."""
    before, _ = _manager(None)
    await _feed(before, MonitorStatus.ALERT, [_c("dx-eu", "Direct Connect")], 0)

    after, spy_after = _manager(None)
    await _feed(after, MonitorStatus.OK, [], 5)

    assert spy_after.recoveries == []


@pytest.mark.asyncio
async def test_the_restored_all_clear_names_the_component(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_c("dx-eu", "AWS Direct Connect")], 0)

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 5)

    assert "AWS Direct Connect" in spy.recoveries[0]


@pytest.mark.asyncio
async def test_several_pending_alerts_all_survive(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT,
                [_c("a", "Compute"), _c("b", "Storage"), _c("c", "Network")], 0)

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 5)

    assert len(spy.recoveries) == 3


@pytest.mark.asyncio
async def test_the_throttle_survives_so_a_restart_is_not_a_re_alert(tmp_path):
    """Restarting must not become a way to spam: the repeat window is preserved."""
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_c("a", "X")], 0)

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.ALERT, [_c("a", "X")], 1)   # inside the window

    assert spy.alerts == []


@pytest.mark.asyncio
async def test_the_repeat_still_fires_once_the_window_passes(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_c("a", "X")], 0)

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.ALERT, [_c("a", "X")], 15)  # past repeat_minutes

    assert len(spy.alerts) == 1


@pytest.mark.asyncio
async def test_a_per_module_alert_also_survives(tmp_path):
    """rockstar's payload is a dict, so its key is the bare module id."""
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, {"hero": "bad"}, 0, module_id="rockstar")

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, {"hero": "ok"}, 5, module_id="rockstar")

    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_a_monitor_error_streak_survives(tmp_path):
    """So a permanently dead monitor does not restart its way out of being reported."""
    path = tmp_path / "state.json"
    before, _ = _manager(path, threshold=3)
    for minute in range(2):
        await _feed(before, MonitorStatus.ERROR, None, minute)

    after, _ = _manager(path, threshold=3)
    assert after._error_state["aws"].consecutive_errors == 2


# ---------------------------------------------------------------------------
# What must NOT be resurrected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_recovered_component_is_not_resurrected(tmp_path):
    path = tmp_path / "state.json"
    before, _ = _manager(path)
    await _feed(before, MonitorStatus.ALERT, [_c("a", "X")], 0)
    await _feed(before, MonitorStatus.OK, [], 1)                 # cleared already

    after, spy = _manager(path)
    await _feed(after, MonitorStatus.OK, [], 5)

    assert spy.recoveries == []


@pytest.mark.asyncio
async def test_stale_state_is_discarded_without_an_all_clear(tmp_path):
    """An alert from days ago must not produce a surprise resolution today."""
    path = tmp_path / "state.json"
    # O alerta e gravado com data de tres dias atras, em vez de depender de o relogio
    # avancar durante o teste: e o passado que precisa ser velho, nao o presente.
    before, _ = _manager(path, max_age=60)
    await _feed(before, MonitorStatus.ALERT, [_c("a", "X")], -60 * 24 * 3)

    after, spy = _manager(path, max_age=60)
    await _feed(after, MonitorStatus.OK, [], 0)

    assert spy.recoveries == []


@pytest.mark.asyncio
async def test_state_just_inside_the_age_limit_is_kept(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path), max_age_minutes=60)
    store.save(
        {"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", {"id": "a", "name": "X"})}, {}
    )
    alerts, _ = store.load(now=_T0 + timedelta(minutes=59))
    assert "aws:a" in alerts


def test_state_just_past_the_age_limit_is_dropped(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path), max_age_minutes=60)
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})
    alerts, _ = store.load(now=_T0 + timedelta(minutes=61))
    assert alerts == {}


def test_an_ok_entry_is_never_written(tmp_path):
    """OK is transient bookkeeping; persisting it would resurrect dropped keys."""
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.OK, None, None, None)}, {})
    assert json.loads(path.read_text())["alerts"] == {}


def test_a_future_timestamp_is_treated_as_corrupt(tmp_path):
    """It would freeze the throttle: the elapsed time never reaches the window."""
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0 + timedelta(days=1), "open", None)}, {})
    alerts, _ = store.load(now=_T0)
    assert alerts == {}


def test_a_slightly_future_timestamp_is_tolerated_as_clock_skew(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0 + timedelta(minutes=2), "o", None)}, {})
    alerts, _ = store.load(now=_T0)
    assert "aws:a" in alerts


# ---------------------------------------------------------------------------
# Bug-Hunter — the file is wrong in every way it can be
# ---------------------------------------------------------------------------

def test_no_path_loads_empty_and_writes_nothing(tmp_path):
    store = StateStore(None)
    assert store.enabled is False
    assert store.load() == ({}, {})
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "o", None)}, {})
    assert list(tmp_path.iterdir()) == []


def test_a_missing_file_loads_empty(tmp_path):
    assert StateStore(str(tmp_path / "nope.json")).load() == ({}, {})


def test_an_empty_file_loads_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("", encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_truncated_json_loads_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "alerts": {"aws:a": {"last_alert', encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_a_json_list_instead_of_an_object_loads_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_a_future_schema_version_is_refused_rather_than_guessed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": SCHEMA_VERSION + 1, "alerts": {"aws:a": {}}}),
                    encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_an_entry_without_a_timestamp_is_dropped_not_crashed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "alerts": {"aws:a": {"last_status_text": "open"}},
    }), encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_an_unparseable_timestamp_is_dropped(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "alerts": {"aws:a": {"last_alert_at": "yesterday-ish"}},
    }), encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        # Sem sufixo de fuso, derivado da ancora: uma data literal aqui envelheceria
        # junto com a suite, que e o defeito que este arquivo acabou de corrigir.
        "alerts": {"aws:a": {"last_alert_at": _T0.replace(tzinfo=None).isoformat()}},
    }), encoding="utf-8")
    alerts, _ = StateStore(str(path)).load(now=_T0 + timedelta(minutes=1))
    assert alerts["aws:a"].last_alert_at == _T0


def test_a_good_entry_survives_a_bad_neighbour(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "alerts": {
            "aws:bad": {"last_alert_at": "not a date"},
            "aws:good": {"last_alert_at": _T0.isoformat(), "last_status_text": "open"},
        },
    }), encoding="utf-8")
    alerts, _ = StateStore(str(path)).load(now=_T0 + timedelta(minutes=1))
    assert list(alerts) == ["aws:good"]


def test_an_entry_that_is_not_an_object_is_dropped(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": SCHEMA_VERSION, "alerts": {"aws:a": "nope"}}),
                    encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_a_last_item_that_is_not_an_object_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "alerts": {"aws:a": {"last_alert_at": _T0.isoformat(), "last_item": "nope"}},
    }), encoding="utf-8")
    alerts, _ = StateStore(str(path)).load(now=_T0)
    assert alerts["aws:a"].last_item is None


def test_a_negative_error_count_is_normalised(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "errors": {"aws": {"consecutive_errors": -5}},
    }), encoding="utf-8")
    _, errors = StateStore(str(path)).load()
    assert errors["aws"].consecutive_errors == 0


def test_state_written_by_a_version_without_last_item_still_loads(tmp_path):
    """Forward compatibility with the file this feature's first release would write."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "alerts": {"aws:a": {"last_alert_at": _T0.isoformat(), "last_status_text": "open"}},
    }), encoding="utf-8")
    alerts, _ = StateStore(str(path)).load(now=_T0)
    assert alerts["aws:a"].last_status_text == "open"


@pytest.mark.asyncio
async def test_an_unwritable_path_does_not_break_the_monitor(tmp_path):
    """Full disk, read-only mount, wrong owner — the monitor keeps monitoring."""
    unwritable = tmp_path / "state.json"
    unwritable.mkdir()                      # a directory where a file must go
    manager, spy = _manager(unwritable)
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "X")], 0)
    assert len(spy.alerts) == 1


@pytest.mark.asyncio
async def test_an_unreadable_file_does_not_break_startup(tmp_path):
    path = tmp_path / "state.json"
    path.mkdir()
    manager, _ = _manager(path)
    assert manager._alert_state == {}


def test_a_failed_write_leaves_the_previous_state_intact(tmp_path, monkeypatch):
    """The temp-file-and-rename is what makes a crash mid-write survivable."""
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})
    good = path.read_text(encoding="utf-8")

    monkeypatch.setattr("os.replace", MagicMock(side_effect=OSError("no space left")))
    store.save({"aws:b": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})

    assert path.read_text(encoding="utf-8") == good


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    monkeypatch.setattr("os.replace", MagicMock(side_effect=OSError("no space left")))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})
    assert list(tmp_path.iterdir()) == []


def test_a_missing_parent_directory_is_created(tmp_path):
    path = tmp_path / "deep" / "nested" / "state.json"
    StateStore(str(path)).save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "o", None)}, {})
    assert path.exists()


def test_a_round_trip_preserves_every_field(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    item = {"id": "dx-eu", "name": "Direct Connect", "status": "open"}
    store.save(
        {"aws:dx-eu": AlertState(MonitorStatus.ALERT, _T0, "open", item)},
        {"steam": MonitorErrorState(3, _T0, "status 403")},
    )
    alerts, errors = store.load(now=_T0)
    assert alerts["aws:dx-eu"].last_alert_at == _T0
    assert alerts["aws:dx-eu"].last_status_text == "open"
    assert alerts["aws:dx-eu"].last_item == item
    assert errors["steam"].consecutive_errors == 3
    assert errors["steam"].last_reason == "status 403"


def test_a_component_name_with_exotic_characters_round_trips(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    item = {"id": "x", "name": "São Paulo, Brazil — (GRU) \"quoted\""}
    store.save({"cf:x": AlertState(MonitorStatus.ALERT, _T0, "partial_outage", item)}, {})
    alerts, _ = store.load(now=_T0)
    assert alerts["cf:x"].last_item["name"] == item["name"]


def test_saving_twice_overwrites_rather_than_appends(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "o", None)}, {})
    store.save({"aws:b": AlertState(MonitorStatus.ALERT, _T0, "o", None)}, {})
    assert list(json.loads(path.read_text())["alerts"]) == ["aws:b"]


def test_an_unchanged_state_is_not_rewritten(tmp_path, monkeypatch):
    """Everything healthy is the common case; it must not cost an fsync per cycle."""
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    state = {"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", None)}
    store.save(state, {})

    replace = MagicMock()
    monkeypatch.setattr("os.replace", replace)
    store.save(state, {})
    store.save(state, {})

    assert replace.call_count == 0


def test_a_changed_state_is_written_again(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.save({"aws:a": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})
    store.save({"aws:b": AlertState(MonitorStatus.ALERT, _T0, "open", None)}, {})
    assert list(json.loads(path.read_text())["alerts"]) == ["aws:b"]


@pytest.mark.asyncio
async def test_a_zero_max_age_keeps_everything(tmp_path):
    """0 means no limit, not "expire immediately" — the difference matters."""
    path = tmp_path / "state.json"
    before, _ = _manager(path, max_age=0)
    await _feed(before, MonitorStatus.ALERT, [_c("a", "X")], 0)

    after, spy = _manager(path, max_age=0)
    await _feed(after, MonitorStatus.OK, [], 60 * 24 * 30)      # a month later

    assert len(spy.recoveries) == 1
