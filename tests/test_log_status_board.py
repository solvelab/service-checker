"""Status board in the log, routine checks below INFO, pretty format (#125).

There is no dashboard, so `kubectl logs` is the panel. In ten minutes of production 119
of 121 lines were a healthy check repeating itself. These tests pin the three changes
that turn the log into something a person can read: routine checks drop to DEBUG, a
status board shows every provider, and a pretty format draws it as a table.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.core import scheduler
from app.core.board import build_board, run_status_board
from app.core.config import (
    NotificationConfig,
    TelegramConfig,
    WebhookConfig,
    drain_config_warnings,
    load_app_config,
)
from app.core.logging import JsonFormatter, PrettyFormatter, configure_logging
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _manager(modules=("oci", "aws", "retell")):
    config = NotificationConfig(
        telegram=TelegramConfig(enabled=False, bot_token=None, chat_ids=[], api_url="x",
                                timestamp_format="%Y", timestamp_zone="UTC"),
        webhook=WebhookConfig(enabled=False, url=None, token=None, header_name="A"),
        repeat_minutes=10,
    )
    return NotificationManager(config, module_ids=modules)


def _r(status, reason=None):
    return MonitorResult(status=status, message=f"{status.value.lower()} message", reason=reason)


OK, ALERT, ERROR = MonitorStatus.OK, MonitorStatus.ALERT, MonitorStatus.ERROR


def _capture(name="test_log_status_board"):
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(f"{name}.{id(records)}")
    logger.handlers = [_Handler()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, records


class Stop(Exception):
    pass


# -- config ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    for name in ("SERVICE_MONITOR_LOG_FORMAT", "SERVICE_MONITOR_STATUS_BOARD_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    drain_config_warnings()
    yield monkeypatch
    drain_config_warnings()


def test_defaults_keep_json_and_a_board_every_fifteen_minutes(env):
    config = load_app_config()
    assert (config.log_format, config.status_board_minutes) == ("json", 15)
    assert drain_config_warnings() == []


def test_pretty_and_zero_are_accepted(env):
    env.setenv("SERVICE_MONITOR_LOG_FORMAT", " Pretty ")
    env.setenv("SERVICE_MONITOR_STATUS_BOARD_MINUTES", "0")
    config = load_app_config()
    assert (config.log_format, config.status_board_minutes) == ("pretty", 0)


def test_unknown_format_and_negative_cadence_warn(env):
    env.setenv("SERVICE_MONITOR_LOG_FORMAT", "yaml")
    env.setenv("SERVICE_MONITOR_STATUS_BOARD_MINUTES", "-5")
    config = load_app_config()
    assert (config.log_format, config.status_board_minutes) == ("json", 15)
    warnings = drain_config_warnings()
    assert any("SERVICE_MONITOR_LOG_FORMAT" in w for w in warnings)
    assert any("SERVICE_MONITOR_STATUS_BOARD_MINUTES" in w for w in warnings)


def test_configure_logging_picks_the_formatter():
    assert isinstance(configure_logging("INFO", "pretty").handlers[0].formatter, PrettyFormatter)
    assert isinstance(configure_logging("INFO").handlers[0].formatter, JsonFormatter)


# -- routine level --------------------------------------------------------------------


async def _levels(results, monkeypatch, raises=None):
    """Run one module loop over `results`; return the level of each monitor_check."""
    script = list(results)

    class Monitor:
        async def check(self, **_):
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    async def stop_when_done(_seconds):
        if not script:
            raise Stop

    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_when_done)
    logger, records = _capture()
    with pytest.raises(Stop):
        await scheduler._run_monitor_loop(
            Monitor(), MagicMock(slug="oci", interval_seconds=60), MagicMock(), logger, None
        )
    return [r.levelname for r in records if getattr(r, "event", None) == "monitor_check"]


@pytest.mark.asyncio
async def test_a_repeated_check_is_debug_and_a_change_keeps_its_level(monkeypatch):
    levels = await _levels(
        [_r(OK), _r(OK), _r(OK), _r(ALERT, "outage"), _r(ALERT, "outage"),
         _r(ALERT, "new incident"), _r(ERROR, "boom"), _r(ERROR, "boom"),
         _r(ERROR, "other"), _r(OK)],
        monkeypatch,
    )
    assert levels == ["INFO", "DEBUG", "DEBUG", "WARNING", "DEBUG",
                      "WARNING", "ERROR", "DEBUG", "ERROR", "INFO"]


@pytest.mark.asyncio
async def test_a_check_that_raises_the_same_way_is_debug_the_second_time(monkeypatch):
    levels = await _levels(
        [RuntimeError("parser"), RuntimeError("parser"), RuntimeError("other"), _r(OK)],
        monkeypatch,
    )
    assert levels == ["ERROR", "DEBUG", "ERROR", "INFO"]


@pytest.mark.asyncio
async def test_the_notifier_still_gets_the_status_level_for_a_routine_check(monkeypatch):
    seen = []

    class Spy:
        async def handle_result(self, **kwargs):
            seen.append(kwargs["level_name"])

    script = [_r(ALERT, "x"), _r(ALERT, "x")]

    class Monitor:
        async def check(self, **_):
            return script.pop(0)

    async def stop(_s):
        if not script:
            raise Stop

    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    logger, _ = _capture()
    with pytest.raises(Stop):
        await scheduler._run_monitor_loop(
            Monitor(), MagicMock(slug="oci", interval_seconds=60), MagicMock(), logger, Spy()
        )
    assert seen == ["WARNING", "WARNING"]


# -- snapshot and board ---------------------------------------------------------------


def test_the_last_error_survives_the_recovery_and_change_is_signalled():
    manager = _manager()
    manager.observe("oci", _r(OK), T0)
    assert manager.status_changed.is_set()  # a first result fills a "—" row: a change
    manager.status_changed.clear()
    manager.observe("oci", _r(OK), T0 + timedelta(seconds=30))
    assert not manager.status_changed.is_set()  # a repeat is not
    manager.observe("oci", _r(ERROR, "ConnectTimeout"), T0 + timedelta(minutes=1))
    assert manager.status_changed.is_set()
    manager.status_changed.clear()
    manager.observe("oci", _r(OK), T0 + timedelta(minutes=2))
    line = manager.module_reports(["oci"])[0]
    assert (line.last_error, line.last_error_at) == ("ConnectTimeout", T0 + timedelta(minutes=1))
    assert manager.status_changed.is_set()


def test_board_rows_carry_reason_or_last_error():
    manager = _manager()
    manager.observe("oci", _r(ERROR, "ConnectTimeout"), T0)
    manager.observe("oci", _r(OK), T0 + timedelta(minutes=5))
    manager.observe("aws", _r(ALERT, "sa-east-1: operational issue"), T0)
    board = build_board(manager, ("oci", "aws", "retell"), T0 - timedelta(hours=3), "2.15.0",
                        T0 + timedelta(minutes=6))
    rows = {row["module"]: row for row in board["providers"]}
    assert rows["oci"]["note"] == "last error 19/11 12:00Z: ConnectTimeout"
    assert rows["aws"]["note"] == "sa-east-1: operational issue"
    assert rows["retell"]["status"] is None
    assert (board["uptime"], board["failing"], board["open_alerts"]) == ("3h 6m", 0, 0)
    json.dumps(board)  # one parseable object


@pytest.mark.asyncio
async def test_board_after_first_round_then_one_board_per_burst_of_changes():
    manager = _manager()
    logger, records = _capture()
    task = asyncio.create_task(run_status_board(
        manager, ("oci", "aws", "retell"), logger, version="1", minutes=0,
        startup_timeout_seconds=5, sleep=lambda _s: asyncio.sleep(0),
    ))
    for module in ("oci", "aws", "retell"):
        manager.observe(module, _r(OK), T0)
    for _ in range(5):
        await asyncio.sleep(0)
    boards = [r for r in records if getattr(r, "event", None) == "status_board"]
    assert len(boards) == 1
    for module in ("oci", "aws", "retell"):
        manager.observe(module, _r(ERROR, "dns"), T0 + timedelta(minutes=1))
    for _ in range(10):
        await asyncio.sleep(0)
    boards = [r for r in records if getattr(r, "event", None) == "status_board"]
    assert len(boards) == 2
    assert [row["status"] for row in boards[1].board["providers"]] == ["ERROR"] * 3
    assert boards[1].board["failing"] == 3
    task.cancel()


@pytest.mark.asyncio
async def test_a_board_goes_out_periodically_when_nothing_changes():
    manager = _manager(modules=("oci",))
    manager.observe("oci", _r(OK), T0)
    logger, records = _capture()
    task = asyncio.create_task(run_status_board(
        manager, ("oci",), logger, version="1", minutes=0.02 / 60, startup_timeout_seconds=5,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    assert sum(getattr(r, "event", None) == "status_board" for r in records) >= 3


@pytest.mark.asyncio
async def test_a_slow_module_does_not_hold_the_first_board():
    manager = _manager(modules=("oci", "aws"))
    manager.observe("oci", _r(OK), T0)
    logger, records = _capture()
    task = asyncio.create_task(run_status_board(
        manager, ("oci", "aws"), logger, version="1", minutes=0, startup_timeout_seconds=0.02,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    events = [getattr(r, "event", None) for r in records]
    assert events[:2] == ["status_board_partial", "status_board"]


# -- formatters -----------------------------------------------------------------------


def _record(message="monitor loop error", level=logging.WARNING, **extra):
    record = logging.LogRecord("t", level, __file__, 1, message, None, None)
    record.created = T0.timestamp()
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_pretty_line_is_short_and_keeps_only_allowlisted_fields():
    text = PrettyFormatter().format(_record(
        "fetch retry", event="fetch_retry", module_id="bitbucket", reason="ConnectTimeout x",
        status="ERROR", token="SECRET", webhook_url="https://chat?key=SECRET",
    ))
    assert text.startswith("2026-11-19 12:00:00Z  WARNI  bitbucket    fetch retry")
    assert 'status=ERROR  reason="ConnectTimeout x"' in text
    assert "SECRET" not in text and "event=" not in text


def _board_record(providers):
    return _record("status board", level=logging.INFO, event="status_board", board={
        "version": "2.15.0", "uptime": "3h 12m", "providers": providers,
        "channels": ["telegram", "alertmanager"], "open_alerts": 1, "failing": 1,
    })


def test_pretty_board_is_an_aligned_table_within_100_columns():
    providers = [
        {"module": "oci", "status": "OK", "since": T0.isoformat(), "checked_at": T0.isoformat(),
         "note": None},
        {"module": "elevenlabs", "status": "ALERT", "since": T0.isoformat(),
         "checked_at": T0.isoformat(), "note": "minor: Increased latency " * 10},
        {"module": "a-very-long-module-name", "status": None, "since": None, "checked_at": None,
         "note": None},
    ]
    text = PrettyFormatter().format(_board_record(providers))
    lines = text.splitlines()
    assert all(len(line) <= 100 for line in lines), [len(line) for line in lines]
    assert "status board ─ v2.15.0 ─ up 3h 12m" in lines[0]
    assert lines[1].split() == ["PROVIDER", "STATUS", "SINCE", "CHECKED", "NOTE"]
    status_col = lines[1].index("STATUS")
    assert [line[status_col:status_col + 5].strip() for line in lines[2:5]] == ["OK", "ALERT", "—"]
    assert lines[3].endswith("…")
    assert "19/11 12:00" in lines[2] and "12:00:00" in lines[2]
    assert lines[-1].split() == ["channels:", "telegram,", "alertmanager", "open", "alerts:", "1",
                                 "failing:", "1"]


def test_json_board_is_one_parseable_object():
    line = json.loads(JsonFormatter().format(_board_record([{"module": "oci", "status": "OK"}])))
    assert line["event"] == "status_board"
    assert line["board"]["providers"][0]["module"] == "oci"


@pytest.mark.asyncio
async def test_open_alerts_counts_only_alerts_not_healthy_entries():
    manager = _manager(modules=("oci", "aws"))
    logger, _ = _capture()
    common = dict(module_config=MagicMock(interval_seconds=60), level_name="INFO",
                  event_name="monitor_check", http_client=MagicMock(), logger=logger)
    await manager.handle_result(module_id="oci", result=_r(OK), event_time=T0, **common)
    await manager.handle_result(module_id="aws", result=_r(OK), event_time=T0, **common)
    assert manager.open_alerts() == 0
    await manager.handle_result(module_id="aws", result=_r(ALERT, "x"), event_time=T0, **common)
    assert manager.open_alerts() == 1


# -- bug-hunter cases -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_module_answering_after_the_partial_board_gets_a_board():
    manager = _manager(modules=("oci", "aws"))
    manager.observe("oci", _r(OK), T0)
    logger, records = _capture()
    task = asyncio.create_task(run_status_board(
        manager, ("oci", "aws"), logger, version="1", minutes=0,
        startup_timeout_seconds=0.02, sleep=lambda _s: asyncio.sleep(0),
    ))
    await asyncio.sleep(0.05)
    manager.observe("aws", _r(OK), T0 + timedelta(minutes=1))
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    boards = [r.board for r in records if getattr(r, "event", None) == "status_board"]
    assert len(boards) == 2
    assert {row["module"]: row["status"] for row in boards[1]["providers"]}["aws"] == "OK"


def test_pretty_line_escapes_newlines_and_control_chars():
    text = PrettyFormatter().format(_record(
        "line1\nforged  INFO  ok", event="monitor_check", reason="bad\nINFO\x1b[2J",
    ))
    assert "\n" not in text and "\x1b" not in text
    assert "line1\\nforged" in text


def test_pretty_board_title_and_channels_escape_control_chars():
    record = _board_record([])
    record.board["version"] = "1\nforged"
    record.board["channels"] = ["tele\x1bgram"]
    text = PrettyFormatter().format(record)
    assert "forged" in text and "\x1b" not in text
    assert text.count("\n") == 2  # title, column header, footer


@pytest.mark.asyncio
async def test_a_new_kind_of_failure_with_an_empty_message_keeps_error(monkeypatch):
    import httpx

    levels = await _levels(
        [httpx.ConnectTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout(""), _r(OK)],
        monkeypatch,
    )
    assert levels == ["ERROR", "ERROR", "DEBUG", "INFO"]


@pytest.mark.asyncio
async def test_an_error_with_the_same_reason_but_another_message_keeps_error(monkeypatch):
    levels = await _levels(
        [MonitorResult(status=ERROR, message="x status request failed", reason=""),
         MonitorResult(status=ERROR, message="x rule evaluation failed", reason="")],
        monkeypatch,
    )
    assert levels == ["ERROR", "ERROR"]


@pytest.mark.asyncio
async def test_a_board_failure_is_logged_once_and_does_not_raise():
    from app.main import _guard

    manager = _manager(modules=("oci",))
    manager.observe("oci", _r(OK), T0)
    manager.module_reports = MagicMock(side_effect=RuntimeError("boom"))
    logger, records = _capture()
    await _guard(
        run_status_board(manager, ("oci",), logger, version="1", minutes=0,
                         startup_timeout_seconds=1),
        logger, "status board stopped", "status_board_failed",
    )
    failed = [r for r in records if getattr(r, "event", None) == "status_board_failed"]
    assert len(failed) == 1 and failed[0].levelno == logging.ERROR


def test_open_alerts_ignores_alerts_of_modules_no_longer_loaded():
    from app.core.notifications import AlertState

    manager = _manager(modules=("oci",))
    manager._alert_state["removed"] = AlertState(last_status=ALERT, last_alert_at=T0)
    manager._alert_state["removed:api"] = AlertState(last_status=ALERT, last_alert_at=T0)
    manager._alert_state["oci:sa-saopaulo-1"] = AlertState(last_status=ALERT, last_alert_at=T0)
    assert manager.open_alerts() == 1
