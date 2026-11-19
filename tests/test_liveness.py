"""Liveness file for the Kubernetes probe (#134).

The app serves no HTTP, so the probe reads a file's age. These tests pin that the file
stops being touched when any module stops completing checks, that a fresh start is not
killed before the first round, and that the probe command fails closed.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app import healthcheck
from app.core import liveness
from app.core.config import drain_config_warnings, load_app_config
from app.core.liveness import MIN_WINDOW_SECONDS, run_liveness, stale_modules, window_seconds
from app.core.types import MonitorResult, MonitorStatus
from tests.test_log_status_board import _capture, _manager

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _config(slug, interval=60, timeout=10.0):
    return MagicMock(slug=slug, interval_seconds=interval, timeout_seconds=timeout)


OK = MonitorResult(status=MonitorStatus.OK, message="ok")


class Stop(Exception):
    pass


def _clock(times):
    times = list(times)
    return lambda: times.pop(0) if len(times) > 1 else times[0]


async def _run(manager, configs, path, ticks, clock_times):
    """Run the liveness loop for `ticks` iterations; return the log records."""
    logger, records = _capture("test_liveness")
    calls = []

    async def sleep(_seconds):
        calls.append(_seconds)
        if len(calls) >= ticks:
            raise Stop

    with pytest.raises(Stop):
        await run_liveness(manager, configs, path, logger, clock=_clock(clock_times), sleep=sleep)
    return records


# -- window ---------------------------------------------------------------------------


def test_window_never_drops_below_the_floor():
    # 3 x 60 s + (3 x 10 s + 2 x 1.5 s) = 213 s: a slow channel or extra fetch attempts
    # would kill a healthy pod, so the floor wins.
    assert window_seconds(_config("oci")) == MIN_WINDOW_SECONDS == 900


def test_a_slow_interval_window_is_three_cycles_plus_the_worst_fetch():
    assert window_seconds(_config("oci", interval=600)) == 1800 + 33


@pytest.mark.parametrize("timeout", [float("nan"), float("inf")])
def test_a_non_finite_timeout_still_has_a_finite_window(timeout):
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    configs = [_config("oci", timeout=timeout)]
    assert stale_modules(manager, configs, T0, T0 + timedelta(days=1)) == ["oci"]


def test_a_delivery_stretched_cycle_is_not_stale():
    # Telegram timing out for 12 modules x 2 chats serializes ~240 s per round.
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    assert stale_modules(manager, [_config("oci")], T0, T0 + timedelta(seconds=60 + 240 + 70)) == []


def test_a_module_not_checked_yet_counts_from_the_start():
    manager = _manager(modules=("oci",))
    configs = [_config("oci")]
    assert stale_modules(manager, configs, T0, T0 + timedelta(seconds=800)) == []
    assert stale_modules(manager, configs, T0, T0 + timedelta(seconds=1000)) == ["oci"]


def test_only_the_overdue_module_is_named():
    manager = _manager(modules=("oci", "aws"))
    manager.observe("oci", OK, T0 + timedelta(seconds=990))
    manager.observe("aws", OK, T0)
    configs = [_config("oci"), _config("aws")]
    assert stale_modules(manager, configs, T0, T0 + timedelta(seconds=1000)) == ["aws"]


def test_a_slow_interval_gets_a_longer_window():
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    configs = [_config("oci", interval=600)]
    assert stale_modules(manager, configs, T0, T0 + timedelta(seconds=1800)) == []


# -- writer ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_file_is_touched_while_every_module_is_on_time(tmp_path):
    path = tmp_path / "alive"
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    records = await _run(manager, [_config("oci")], str(path), 1, [T0, T0 + timedelta(seconds=30)])
    assert path.exists()
    assert records == []


@pytest.mark.asyncio
async def test_a_hung_module_stops_the_file_and_is_logged_once(tmp_path):
    path = tmp_path / "alive"
    manager = _manager(modules=("oci", "aws"))
    manager.observe("oci", OK, T0)
    manager.observe("aws", OK, T0)
    late = T0 + timedelta(seconds=1000)
    records = await _run(manager, [_config("oci"), _config("aws")], str(path), 3, [T0, late, late, late])
    assert not path.exists()
    stale = [r for r in records if getattr(r, "event", None) == "liveness_stale"]
    assert len(stale) == 1 and stale[0].modules == ["oci", "aws"]
    assert stale[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_recovery_is_logged_and_the_file_written_again(tmp_path):
    path = tmp_path / "alive"
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    late = T0 + timedelta(seconds=1000)
    times = iter([T0, late, late + timedelta(seconds=30)])
    logger, records = _capture("test_liveness")
    calls = []

    async def sleep(_seconds):
        calls.append(_seconds)
        if len(calls) == 1:
            manager.observe("oci", OK, late + timedelta(seconds=10))
        if len(calls) >= 2:
            raise Stop

    with pytest.raises(Stop):
        await run_liveness(manager, [_config("oci")], str(path), logger,
                           clock=lambda: next(times), sleep=sleep)
    assert [r.event for r in records] == ["liveness_stale", "liveness_fresh"]
    assert path.exists()


@pytest.mark.asyncio
async def test_an_empty_path_writes_nothing(tmp_path):
    manager = _manager(modules=("oci",))
    logger, records = _capture("test_liveness")
    await run_liveness(manager, [_config("oci")], None, logger)
    assert list(tmp_path.iterdir()) == [] and records == []


@pytest.mark.asyncio
async def test_a_write_failure_is_logged_once_then_its_recovery(tmp_path, monkeypatch):
    path = tmp_path / "alive"
    manager = _manager(modules=("oci",))
    manager.observe("oci", OK, T0)
    outcomes = [PermissionError("denied"), PermissionError("denied"), None]

    def touch(_path):
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome

    monkeypatch.setattr(liveness, "touch", touch)
    records = await _run(manager, [_config("oci")], str(path), 3, [T0])
    assert [r.event for r in records] == ["liveness_write_failed", "liveness_write_recovered"]
    assert records[0].reason == "PermissionError: denied"


# -- config ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [(None, "/tmp/service-checker-alive"), ("", None), ("  ", None), ("/run/alive", "/run/alive")],
)
def test_liveness_path_from_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SERVICE_MONITOR_LIVENESS_PATH", raising=False)
    else:
        monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", value)
    assert load_app_config().liveness_path == expected
    drain_config_warnings()


# -- probe command --------------------------------------------------------------------


def _aged(tmp_path, seconds):
    path = tmp_path / "alive"
    path.touch()
    then = time.time() - seconds
    os.utime(path, (then, then))
    return str(path)


def test_probe_passes_on_a_fresh_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", _aged(tmp_path, 10))
    assert healthcheck.main([]) == 0
    assert capsys.readouterr().out.startswith("ok: liveness file is")


@pytest.mark.parametrize("argv, age, code", [([], 300, 1), (["400"], 300, 0), (["60"], 90, 1)])
def test_probe_compares_the_age_with_max_age(tmp_path, monkeypatch, argv, age, code):
    monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", _aged(tmp_path, age))
    assert healthcheck.main(argv) == code


def test_probe_fails_on_a_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", str(tmp_path / "nothing"))
    assert healthcheck.main([]) == 1
    assert "unreadable: FileNotFoundError" in capsys.readouterr().out


def test_probe_fails_when_liveness_is_off(monkeypatch, capsys):
    monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", "")
    assert healthcheck.main([]) == 1
    assert "liveness is off" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["abc", "0", "-5", "nan", "inf", "1e309"])
def test_probe_rejects_a_bad_max_age(tmp_path, monkeypatch, arg):
    monkeypatch.setenv("SERVICE_MONITOR_LIVENESS_PATH", _aged(tmp_path, 1))
    assert healthcheck.main([arg]) == 1


def test_probe_fails_on_a_file_from_the_future(tmp_path):
    path = _aged(tmp_path, -3600)
    code, reason = healthcheck.check(path, 120, time.time())
    assert code == 1 and "in the future" in reason


@pytest.mark.asyncio
async def test_a_liveness_bug_keeps_the_file_and_is_logged_once(tmp_path):
    path = tmp_path / "alive"
    manager = _manager(modules=("oci",))
    manager.module_reports = MagicMock(side_effect=RuntimeError("boom"))
    records = await _run(manager, [_config("oci")], str(path), 3, [T0])
    assert path.exists()
    failed = [r for r in records if getattr(r, "event", None) == "liveness_check_failed"]
    assert len(failed) == 1 and failed[0].reason == "RuntimeError: boom"
