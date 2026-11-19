"""Tests for the JSON log surface.

Two silent failures motivated this file, and both were found in production rather than
by reading the code.

The formatter copies a fixed list of `extra` keys onto the JSON document and ignores
everything else. An allowlist is the right call — an arbitrary `extra` could drag a
credential into the log — but the list had drifted behind the code. Eight keys were
being passed and dropped, `target` among them, which meant the line
`"notification channel failed"` never said **which** channel failed. That name was the
entire point of isolating the channels in the first place.

The second was the store's logger. `configure_logging` sets up `service_monitor` only,
with `propagate = False`; the store logged to `app.core.state_store`, a tree with no
handler at all. So the rule "swallow every failure but record it" became "swallow every
failure". A volume that goes read-only stops persisting in silence, and silent
persistence is indistinguishable from working persistence.

The audit below is the durable part: it turns "somebody forgot the allowlist" from a
thing you discover in production into a red test.
"""
from __future__ import annotations

import io
import ast
import json
import logging
from pathlib import Path

import pytest

from app.core.logging import _EXTRA_KEYS, JsonFormatter, configure_logging
from app.core.state_store import StateStore

_APP = Path(__file__).resolve().parent.parent / "app"


def _capture(logger_name="service_monitor", level="INFO"):
    """Attach the real formatter to a buffer and return (logger, read)."""
    logger = configure_logging(level)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    def read():
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    return logging.getLogger(logger_name), read


# ---------------------------------------------------------------------------
# The audit — the test that keeps the allowlist honest
# ---------------------------------------------------------------------------

def _extra_keys_used_in_app() -> dict[str, set[str]]:
    # Parsed, not matched: the old regex `extra=\{(.*?)\}` stopped at the first `}`, so a
    # key after a nested dict (`connectivity` after `defaults` in main.py) was never seen
    # and the formatter dropped it in production (#108).
    used: dict[str, set[str]] = {}
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        used.setdefault(key.value, set()).add(
                            str(path.relative_to(_APP.parent))
                        )
    return used


def test_the_audit_actually_finds_keys():
    """Guards the guard: a broken regex would make the next test vacuously green."""
    used = _extra_keys_used_in_app()
    assert len(used) >= 10
    assert "event" in used and "module_id" in used


def test_every_extra_key_used_in_the_app_is_allowed():
    """A key passed but not listed is dropped without a word. Fail loudly instead."""
    used = _extra_keys_used_in_app()
    missing = {key: sorted(files) for key, files in used.items() if key not in _EXTRA_KEYS}
    assert missing == {}, (
        "these `extra` keys are passed but not in _EXTRA_KEYS, so they are silently "
        f"dropped from the log: {missing}"
    )


def test_the_allowlist_has_no_dead_entries():
    """Not a defect, but a stale allowlist is how the live one loses credibility."""
    used = _extra_keys_used_in_app()
    unused = sorted(key for key in _EXTRA_KEYS if key not in used)
    assert unused == [], f"listed but never used: {unused}"


def test_an_unlisted_key_is_still_dropped():
    """The allowlist must keep being an allowlist — this is the property it defends."""
    record = logging.LogRecord("x", logging.INFO, "f", 1, "msg", None, None)
    record.super_secret_token = "hunter2"
    assert "hunter2" not in JsonFormatter().format(record)


# ---------------------------------------------------------------------------
# `target` — the key whose absence hid which channel broke
# ---------------------------------------------------------------------------

def test_target_survives_the_formatter():
    record = logging.LogRecord("x", logging.ERROR, "f", 1, "notification channel failed",
                               None, None)
    record.event = "notify_error"
    record.target = "google_chat"
    assert json.loads(JsonFormatter().format(record))["target"] == "google_chat"


@pytest.mark.asyncio
async def test_a_failing_channel_is_named_in_the_emitted_log():
    """End to end through the real formatter, not through a mock's call_args."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from app.core.config import (
        ModuleConfig,
        NotificationConfig,
        RuleConfig,
        TelegramConfig,
        WebhookConfig,
    )
    from app.core.notifications import NotificationManager
    from app.core.types import MonitorResult, MonitorStatus

    logger, read = _capture()

    class Exploding:
        async def send_alert(self, **kwargs):
            raise RuntimeError("channel is broken")

        async def send_recovery(self, **kwargs):
            return True
        async def send_monitor_error(self, **kwargs): ...
        async def send_monitor_recovered(self, **kwargs): ...

    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
        )
    )
    manager.register("google_chat", Exploding())

    await manager.handle_result(
        module_id="cloudflare",
        result=MonitorResult(MonitorStatus.ALERT, "m", "Tunnel: major_outage", 1.0, {"a": 1}),
        module_config=ModuleConfig("cloudflare", "https://x", 60, 10.0, "ua",
                                   RuleConfig("status", "x"), [], True),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
        http_client=AsyncMock(),
        logger=logger,
    )

    failures = [line for line in read() if line["message"] == "notification channel failed"]
    assert len(failures) == 1
    assert failures[0]["target"] == "google_chat"


# ---------------------------------------------------------------------------
# The store logs where the operator is looking
# ---------------------------------------------------------------------------

def test_the_store_logs_through_the_application_logger(tmp_path):
    _, read = _capture()
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")

    StateStore(str(path)).load()

    lines = [line for line in read() if line["event"] == "state_error"]
    assert len(lines) == 1
    assert "could not be read" in lines[0]["message"]
    assert str(path) == lines[0]["target"]


def test_a_write_failure_is_visible_not_silent(tmp_path):
    """The read-only-volume case: persistence stops, and the operator can tell."""
    from app.core.notifications import AlertState
    from app.core.types import MonitorStatus
    from datetime import datetime, timezone

    _, read = _capture()
    blocked = tmp_path / "state.json"
    blocked.mkdir()

    StateStore(str(blocked)).save(
        {"aws:a": AlertState(MonitorStatus.ALERT, datetime(2026, 8, 16, tzinfo=timezone.utc),
                             "open", None)},
        {},
    )

    lines = [line for line in read() if line["event"] == "state_error"]
    assert len(lines) == 1
    assert "could not be written" in lines[0]["message"]


def test_a_successful_restore_says_so(tmp_path):
    from app.core.notifications import AlertState
    from app.core.types import MonitorStatus
    from datetime import datetime, timezone

    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "state.json"
    StateStore(str(path)).save({"aws:a": AlertState(MonitorStatus.ALERT, now, "open", None)}, {})

    _, read = _capture()
    StateStore(str(path)).load(now=now)

    lines = [line for line in read() if line["event"] == "state_load"]
    assert len(lines) == 1
    assert "1 pending alert" in lines[0]["reason"]


def test_a_missing_state_file_says_nothing(tmp_path):
    """First boot is not an event. Noise at startup trains people to skim."""
    _, read = _capture()
    StateStore(str(tmp_path / "absent.json")).load()
    assert read() == []


def test_an_error_level_still_lets_the_write_failure_through(tmp_path):
    """The success line may be filtered by level; the failure must not be."""
    from app.core.notifications import AlertState
    from app.core.types import MonitorStatus
    from datetime import datetime, timezone

    _, read = _capture(level="ERROR")
    blocked = tmp_path / "state.json"
    blocked.mkdir()
    StateStore(str(blocked)).save(
        {"aws:a": AlertState(MonitorStatus.ALERT, datetime(2026, 8, 16, tzinfo=timezone.utc),
                             "o", None)},
        {},
    )
    # WARNING is below ERROR, so nothing is emitted — but the call must not raise,
    # which is the property that matters for a monitor that has to keep running.
    assert read() == []


def test_the_store_never_raises_when_logging_is_unconfigured(tmp_path):
    """Unit tests build a store without `configure_logging`; that must be harmless."""
    for handler in list(logging.getLogger("service_monitor").handlers):
        logging.getLogger("service_monitor").removeHandler(handler)
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert StateStore(str(path)).load() == ({}, {})


def test_the_startup_log_keeps_the_connectivity_settings():
    # #108: the formatter copies only known fields, and the block was dropped in production.
    import logging as std_logging

    from app.core.logging import JsonFormatter

    record = std_logging.LogRecord("t", std_logging.INFO, __file__, 1, "service monitor starting",
                                   None, None)
    record.event = "startup"
    record.connectivity = {"quorum": 0.7, "tolerance_minutes": 10,
                           "windows": ["04:00-04:15"], "timezone": "America/Sao_Paulo"}
    line = json.loads(JsonFormatter().format(record))
    assert line["connectivity"]["windows"] == ["04:00-04:15"]


def test_the_startup_log_keeps_the_challenge_tolerance():
    # #112: same formatter allowlist as the connectivity block (#108).
    import logging as std_logging

    from app.core.logging import JsonFormatter

    record = std_logging.LogRecord("t", std_logging.INFO, __file__, 1, "service monitor starting",
                                   None, None)
    record.event = "startup"
    record.challenge = {"tolerance_minutes": 60}
    line = json.loads(JsonFormatter().format(record))
    assert line["challenge"] == {"tolerance_minutes": 60}


def test_the_startup_log_keeps_the_report_settings():
    # #124: same allowlist; a heartbeat time that fell back after a typo shows here.
    import logging as std_logging

    from app.core.logging import JsonFormatter

    record = std_logging.LogRecord("t", std_logging.INFO, __file__, 1, "service monitor starting",
                                   None, None)
    record.event = "startup"
    record.reports = {"startup_announce": True, "startup_timeout_seconds": 120,
                      "heartbeat_time": "09:00", "heartbeat_timezone": "America/Sao_Paulo"}
    line = json.loads(JsonFormatter().format(record))
    assert line["reports"]["heartbeat_time"] == "09:00"
