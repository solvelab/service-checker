"""Startup announcement and daily heartbeat (#124).

Silence on a channel used to mean either "every provider is healthy" or "the checker
is not running, or cannot deliver". These tests pin the two messages that tell those
apart: the announcement after the first round of checks, and the daily heartbeat whose
absence is the alarm. The schedule runs on an injected clock and an injected sleep, so
the hour and the retries are tested without waiting for them.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.core.config import (
    AlertmanagerConfig,
    GoogleChatConfig,
    NotificationConfig,
    TelegramConfig,
    WebhookConfig,
    drain_config_warnings,
    load_app_config,
)
from app.core.notifications import NotificationManager
from app.core.reports import (
    REPORT_ATTEMPTS,
    REPORT_RETRY_SECONDS,
    build_report,
    format_uptime,
    next_occurrence,
    run_status_reports,
    short_reason,
    summary_counts,
)
from app.core.types import (
    ModuleReport,
    MonitorResult,
    MonitorStatus,
    StatusReport,
    StatusReporter,
)
from app.notifications.google_chat.notifier import GoogleChatNotifier
from app.notifications.telegram.notifier import TelegramNotifier, _render_report
from app.notifications.webhook.notifier import WebhookNotifier

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)  # 09:00 in Sao Paulo
WEBHOOK = "https://chat.googleapis.com/v1/spaces/AAQ/messages?key=SECRETKEY&token=SECRETTOKEN"
BOT_TOKEN = "123:SECRETBOT"


# -- helpers ----------------------------------------------------------------


def _notification_config(**overrides) -> NotificationConfig:
    base = dict(
        telegram=TelegramConfig(
            enabled=False, bot_token=None, chat_ids=[], api_url="https://api.telegram.org",
            timestamp_format="%Y", timestamp_zone="UTC",
        ),
        webhook=WebhookConfig(enabled=False, url=None, token=None, header_name="Authorization"),
        repeat_minutes=10,
        startup_timeout_seconds=5,
    )
    base.update(overrides)
    return NotificationConfig(**base)


class Channel:
    """A human channel that answers from a script of booleans (last one repeats)."""

    def __init__(self, answers=(True,)):
        self.answers = list(answers)
        self.reports = []

    async def send_alert(self, **_):
        return True

    send_recovery = send_monitor_error = send_monitor_recovered = send_alert

    async def send_status_report(self, *, report, http_client, logger):
        self.reports.append(report)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class RecordOnly:
    """A channel without `send_status_report`, like Alertmanager."""

    async def send_alert(self, **_):
        return True

    send_recovery = send_monitor_error = send_monitor_recovered = send_alert


def _manager(modules=("oci", "aws"), channels=None, **config):
    manager = NotificationManager(_notification_config(**config), module_ids=modules)
    for name, channel in (channels or {}).items():
        manager.register(name, channel)
    return manager


def _ok(reason=None):
    return MonitorResult(status=MonitorStatus.OK, message="ok", reason=reason)


def _alert(reason="partial outage"):
    return MonitorResult(status=MonitorStatus.ALERT, message="degraded", reason=reason)


def _error(reason="no target components matched filter"):
    return MonitorResult(status=MonitorStatus.ERROR, message="failed", reason=reason)


class Clock:
    """Wall clock moved only by the injected sleep."""

    def __init__(self, now: datetime):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class StopAfter(Exception):
    pass


def _logger():
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(f"test_status_reports.{id(records)}")
    logger.handlers = [_Handler()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, records


def _events(records):
    return [getattr(r, "event", None) for r in records]


# -- config -----------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    for name in (
        "NOTIFICATION_STARTUP_ANNOUNCE", "NOTIFICATION_STARTUP_TIMEOUT_SECONDS",
        "NOTIFICATION_HEARTBEAT_TIME", "NOTIFICATION_HEARTBEAT_TIMEZONE",
    ):
        monkeypatch.delenv(name, raising=False)
    drain_config_warnings()
    yield monkeypatch
    drain_config_warnings()


def test_defaults_announce_and_beat_at_nine_in_sao_paulo(env):
    config = load_app_config().notifications
    assert config.startup_announce is True
    assert config.startup_timeout_seconds == 120
    assert config.heartbeat_minute == 9 * 60
    assert config.heartbeat_timezone == "America/Sao_Paulo"
    assert drain_config_warnings() == []


def test_empty_heartbeat_time_switches_it_off(env):
    env.setenv("NOTIFICATION_HEARTBEAT_TIME", "")
    assert load_app_config().notifications.heartbeat_minute is None
    assert drain_config_warnings() == []


@pytest.mark.parametrize("raw", ["9h", "25:00", "09:60", "9:5", "nine"])
def test_invalid_heartbeat_time_falls_back_to_nine_loudly(env, raw):
    env.setenv("NOTIFICATION_HEARTBEAT_TIME", raw)
    assert load_app_config().notifications.heartbeat_minute == 9 * 60
    assert any("NOTIFICATION_HEARTBEAT_TIME" in w for w in drain_config_warnings())


def test_heartbeat_time_and_zone_are_read(env):
    env.setenv("NOTIFICATION_HEARTBEAT_TIME", "07:30")
    env.setenv("NOTIFICATION_HEARTBEAT_TIMEZONE", "Europe/Lisbon")
    config = load_app_config().notifications
    assert (config.heartbeat_minute, config.heartbeat_timezone) == (450, "Europe/Lisbon")


def test_announce_can_be_switched_off_and_bad_timeout_warns(env):
    env.setenv("NOTIFICATION_STARTUP_ANNOUNCE", "false")
    env.setenv("NOTIFICATION_STARTUP_TIMEOUT_SECONDS", "0")
    config = load_app_config().notifications
    assert config.startup_announce is False
    assert config.startup_timeout_seconds == 120
    assert any("NOTIFICATION_STARTUP_TIMEOUT_SECONDS" in w for w in drain_config_warnings())


# -- next occurrence ----------------------------------------------------------


def test_next_occurrence_is_today_before_the_hour_and_tomorrow_at_or_after_it():
    before = datetime(2026, 11, 19, 11, 59, tzinfo=timezone.utc)  # 08:59 local
    assert next_occurrence(before, 540, SAO_PAULO) == T0
    assert next_occurrence(T0, 540, SAO_PAULO) == T0 + timedelta(days=1)
    assert next_occurrence(T0 + timedelta(seconds=20), 540, SAO_PAULO) == T0 + timedelta(days=1)


def test_next_occurrence_uses_the_local_date_not_the_utc_one():
    # 01:00 UTC on the 20th is still the 19th, 22:00, in Sao Paulo.
    late = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)
    assert next_occurrence(late, 23 * 60, SAO_PAULO) == datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)


# -- snapshot -------------------------------------------------------------------


def test_snapshot_keeps_since_while_the_status_holds_and_resets_on_change():
    manager = _manager()
    manager.observe("oci", _ok(), T0)
    manager.observe("oci", _ok(), T0 + timedelta(minutes=1))
    line = manager.module_reports(["oci"])[0]
    assert (line.since, line.checked_at) == (T0, T0 + timedelta(minutes=1))
    manager.observe("oci", _alert(), T0 + timedelta(minutes=2))
    line = manager.module_reports(["oci"])[0]
    assert (line.status, line.since, line.reason) == (
        MonitorStatus.ALERT, T0 + timedelta(minutes=2), "partial outage"
    )


def test_first_round_completes_only_when_every_module_answered():
    manager = _manager(modules=("oci", "aws"))
    manager.observe("oci", _ok(), T0)
    assert not manager.first_round.is_set()
    assert manager.module_reports(["oci", "aws"])[1] == ModuleReport("aws", None)
    manager.observe("aws", _error(), T0)
    assert manager.first_round.is_set()


@pytest.mark.asyncio
async def test_handle_result_feeds_the_snapshot():
    manager = _manager(modules=("oci",))
    logger, _ = _logger()
    await manager.handle_result(
        module_id="oci", result=_error(), module_config=MagicMock(interval_seconds=60),
        level_name="ERROR", event_name="monitor_check", event_time=T0,
        http_client=MagicMock(), logger=logger,
    )
    assert manager.module_reports(["oci"])[0].status == MonitorStatus.ERROR
    assert manager.first_round.is_set()


# -- channels that report -------------------------------------------------------


def test_the_three_human_channels_report_and_alertmanager_does_not():
    gc = GoogleChatConfig(enabled=True, webhook_url=WEBHOOK, min_interval_seconds=0, thread_by_check=True)
    tg = TelegramConfig(enabled=True, bot_token=BOT_TOKEN, chat_ids=["1"], api_url="https://x",
                        timestamp_format="%Y", timestamp_zone="UTC")
    wh = WebhookConfig(enabled=True, url="https://hook", token=None, header_name="Authorization")
    am = AlertmanagerConfig(enabled=True, url="http://am:9093", token=None, header_name="Authorization",
                            resolve_after_seconds=0, extra_labels={})
    manager = NotificationManager(
        _notification_config(telegram=tg, webhook=wh, google_chat=gc, alertmanager=am),
        module_ids=("oci",),
    )
    assert set(manager.channel_names()) == {"telegram", "webhook", "google_chat", "alertmanager"}
    assert set(manager.report_channels()) == {"telegram", "webhook", "google_chat"}
    for cls in (TelegramNotifier, WebhookNotifier, GoogleChatNotifier):
        assert isinstance(cls.__new__(cls), StatusReporter), cls.__name__


@pytest.mark.asyncio
async def test_report_skips_record_only_channels():
    human, record = Channel(), RecordOnly()
    manager = _manager(channels={"human": human, "alertmanager": record})
    logger, records = _logger()
    report = build_report(manager, "startup", ("oci",), T0, "9.9.9", T0)
    assert await manager.send_status_report(report, MagicMock(), logger) is True
    assert len(human.reports) == 1
    assert "notify_error" not in _events(records)


# -- schedule -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_waits_for_the_first_round_and_reports_every_module():
    human = Channel()
    manager = _manager(channels={"human": human}, heartbeat_minute=None)
    logger, records = _logger()
    task = asyncio.create_task(run_status_reports(
        manager, manager.config, ("oci", "aws"), MagicMock(), logger, version="9.9.9",
    ))
    await asyncio.sleep(0)
    assert human.reports == []
    manager.observe("oci", _ok(), T0)
    manager.observe("aws", _error(), T0)
    await asyncio.wait_for(task, 1)
    (report,) = human.reports
    assert report.kind == "startup" and report.version == "9.9.9"
    assert [(m.module_id, m.status) for m in report.modules] == [
        ("oci", MonitorStatus.OK), ("aws", MonitorStatus.ERROR)
    ]
    assert report.modules[1].reason == "no target components matched filter"
    assert "startup_announced" in _events(records)


@pytest.mark.asyncio
async def test_a_slow_module_does_not_hold_the_announcement():
    human = Channel()
    manager = _manager(channels={"human": human}, heartbeat_minute=None, startup_timeout_seconds=0.05)
    manager.observe("oci", _ok(), T0)
    logger, records = _logger()
    await asyncio.wait_for(run_status_reports(
        manager, manager.config, ("oci", "aws"), MagicMock(), logger, version="1",
    ), 1)
    (report,) = human.reports
    assert report.modules[1].status is None
    partial = [r for r in records if getattr(r, "event", None) == "status_report_partial"]
    assert partial and partial[0].modules == ["aws"]


@pytest.mark.asyncio
async def test_announce_off_sends_nothing_at_startup():
    human = Channel()
    manager = _manager(channels={"human": human}, startup_announce=False, heartbeat_minute=None)
    logger, _ = _logger()
    await asyncio.wait_for(run_status_reports(
        manager, manager.config, ("oci",), MagicMock(), logger, version="1"), 1)
    assert human.reports == []


@pytest.mark.asyncio
async def test_without_a_human_channel_the_reports_are_skipped_loudly():
    manager = _manager(channels={"alertmanager": RecordOnly()}, heartbeat_minute=540)
    logger, records = _logger()
    await asyncio.wait_for(run_status_reports(
        manager, manager.config, ("oci",), MagicMock(), logger, version="1"), 1)
    assert "status_report_skip" in _events(records)


@pytest.mark.asyncio
async def test_a_refused_report_is_retried_until_someone_takes_it():
    human = Channel(answers=(False, False, True))
    manager = _manager(channels={"human": human}, heartbeat_minute=None)
    manager.observe("oci", _ok(), T0)
    manager.observe("aws", _ok(), T0)
    clock = Clock(T0)
    logger, records = _logger()
    await run_status_reports(manager, manager.config, ("oci", "aws"), MagicMock(), logger,
                             version="1", clock=clock, sleep=clock.sleep)
    assert len(human.reports) == 3
    assert clock.sleeps == [REPORT_RETRY_SECONDS, REPORT_RETRY_SECONDS]
    assert "status_report_failed" not in _events(records)


@pytest.mark.asyncio
async def test_a_report_nobody_takes_gives_up_after_the_limit_and_says_so():
    human = Channel(answers=(False,))
    manager = _manager(channels={"human": human}, heartbeat_minute=None)
    manager.observe("oci", _ok(), T0)
    manager.observe("aws", _ok(), T0)
    clock = Clock(T0)
    logger, records = _logger()
    await run_status_reports(manager, manager.config, ("oci", "aws"), MagicMock(), logger,
                             version="1", clock=clock, sleep=clock.sleep)
    assert len(human.reports) == REPORT_ATTEMPTS
    assert _events(records).count("status_report_failed") == 1


@pytest.mark.asyncio
async def test_a_channel_that_raises_does_not_stop_the_report():
    class Raises(Channel):
        async def send_status_report(self, **_):
            raise RuntimeError("boom")

    human = Channel()
    manager = _manager(channels={"broken": Raises(), "human": human}, heartbeat_minute=None)
    manager.observe("oci", _ok(), T0)
    manager.observe("aws", _ok(), T0)
    logger, records = _logger()
    await run_status_reports(manager, manager.config, ("oci", "aws"), MagicMock(), logger, version="1")
    assert len(human.reports) == 1
    assert "notify_error" in _events(records)


async def _run_heartbeats(manager, clock, count):
    """Run the schedule until `count` heartbeats went out, then stop it."""
    human = manager._notifiers["human"]

    async def sleep(seconds):
        await clock.sleep(seconds)
        if sum(r.kind == "heartbeat" for r in human.reports) >= count:
            raise StopAfter

    logger, _ = _logger()
    with pytest.raises(StopAfter):
        await run_status_reports(manager, manager.config, ("oci", "aws"), MagicMock(), logger,
                                 version="1", clock=clock, sleep=sleep)
    return human.reports


@pytest.mark.asyncio
async def test_heartbeat_goes_out_daily_at_the_configured_local_hour():
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=540,
                       heartbeat_timezone="America/Sao_Paulo")
    manager.observe("oci", _ok(), T0)
    manager.observe("aws", _alert(), T0)
    clock = Clock(T0 - timedelta(hours=3))  # 06:00 local
    reports = await _run_heartbeats(manager, clock, 2)
    beats = [r for r in reports if r.kind == "heartbeat"]
    assert [b.sent_at for b in beats] == [T0, T0 + timedelta(days=1)]
    assert beats[0].sent_at - beats[0].started_at == timedelta(hours=3)


@pytest.mark.asyncio
async def test_a_restart_right_after_the_heartbeat_waits_for_tomorrow():
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=540)
    clock = Clock(T0 + timedelta(seconds=20))
    reports = await _run_heartbeats(manager, clock, 1)
    assert [r.sent_at for r in reports] == [T0 + timedelta(days=1)]


@pytest.mark.asyncio
async def test_a_sleep_that_wakes_early_does_not_send_before_the_hour_or_twice():
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=540)
    clock = Clock(T0 - timedelta(minutes=10))
    human = manager._notifiers["human"]

    async def early(seconds):
        # Wakes one second short every time.
        await clock.sleep(max(seconds - 1, 0.5))
        if len(human.reports) >= 2:
            raise StopAfter

    logger, _ = _logger()
    with pytest.raises(StopAfter):
        await run_status_reports(manager, manager.config, ("oci",), MagicMock(), logger,
                                 version="1", clock=clock, sleep=early)
    assert all(r.sent_at >= T0 for r in human.reports)
    assert human.reports[1].sent_at >= T0 + timedelta(days=1)


# -- rendering --------------------------------------------------------------------


def _report(kind="startup", modules=None):
    manager = _manager(modules=("oci", "elevenlabs", "aws"))
    manager.register("telegram", Channel())
    manager.register("alertmanager", RecordOnly())
    manager.observe("oci", _ok(), T0)
    manager.observe("elevenlabs", _alert("minor: <Increased> latency & errors"), T0)
    return build_report(manager, kind, modules or ("oci", "elevenlabs", "aws"),
                        T0 - timedelta(hours=26, minutes=5), "2.14.0", T0, zone="America/Sao_Paulo")


def test_helpers():
    assert format_uptime(timedelta(days=1, hours=2, minutes=5)) == "1d 2h 5m"
    assert format_uptime(timedelta(minutes=-3)) == "0m"
    assert short_reason("a" * 300).endswith("…") and len(short_reason("a" * 300)) == 200
    assert summary_counts(_report()) == "1 OK · 1 ALERT · 1 no result"


def test_telegram_render_escapes_and_lists_every_provider():
    text = _render_report(_report("heartbeat"))
    assert "Service checker still running" in text
    assert "&lt;Increased&gt; latency &amp; errors" in text
    assert "<Increased>" not in text
    assert "<b>aws</b> no result yet" in text
    assert "Uptime:</b> 1d 2h 5m" in text
    assert "Channels:</b> telegram, alertmanager" in text


@pytest.mark.asyncio
async def test_telegram_sends_a_new_message_to_every_chat_and_counts_partial_success():
    config = TelegramConfig(enabled=True, bot_token=BOT_TOKEN, chat_ids=["1", "2"],
                            api_url="https://api.telegram.org", timestamp_format="%Y",
                            timestamp_zone="UTC", mode="daily_summary")
    notifier = TelegramNotifier(config)
    ok, bad = MagicMock(status_code=200), MagicMock(status_code=400, text="chat not found")
    ok.json.return_value = {"ok": True, "result": {"message_id": 7}}
    bad.json.return_value = {"ok": False, "description": "chat not found"}
    client = MagicMock()
    client.post = AsyncMock(side_effect=[bad, ok])
    logger, records = _logger()
    assert await notifier.send_status_report(report=_report(), http_client=client, logger=logger)
    assert [c.kwargs["json"]["chat_id"] for c in client.post.call_args_list] == ["1", "2"]
    assert all(c.args[0].endswith("/sendMessage") for c in client.post.call_args_list)
    assert notifier.export_state() is None or notifier.export_state().get("message_ids", {}) == {}
    assert not any(BOT_TOKEN in r.getMessage() + str(getattr(r, "reason", "")) for r in records)


@pytest.mark.asyncio
async def test_google_chat_report_is_a_card_outside_any_thread_and_hides_the_url():
    notifier = GoogleChatNotifier(
        GoogleChatConfig(enabled=True, webhook_url=WEBHOOK, min_interval_seconds=0, thread_by_check=True)
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    logger, _ = _logger()
    assert await notifier.send_status_report(report=_report(), http_client=client, logger=logger)
    url, body = client.post.call_args.args[0], client.post.call_args.kwargs["json"]
    assert url == WEBHOOK and "thread" not in body
    text = json.dumps(body)
    assert "Service checker online" in text and "elevenlabs" in text and "no result yet" in text

    client.post = AsyncMock(return_value=MagicMock(status_code=500, text="boom"))
    logger, records = _logger()
    assert not await notifier.send_status_report(report=_report(), http_client=client, logger=logger)
    dumped = " ".join(r.getMessage() + str(r.__dict__) for r in records)
    assert "SECRETKEY" not in dumped and "SECRETTOKEN" not in dumped


@pytest.mark.asyncio
async def test_webhook_report_payload():
    notifier = WebhookNotifier(WebhookConfig(enabled=True, url="https://hook", token="t",
                                             header_name="Authorization"))
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=204))
    logger, _ = _logger()
    assert await notifier.send_status_report(report=_report("heartbeat"), http_client=client, logger=logger)
    payload = client.post.call_args.kwargs["json"]
    assert (payload["event"], payload["status"], payload["version"]) == ("heartbeat", "HEARTBEAT", "2.14.0")
    assert payload["uptime_seconds"] == (26 * 60 + 5) * 60
    assert [m["status"] for m in payload["modules"]] == ["OK", "ALERT", None]
    assert client.post.call_args.kwargs["headers"] == {"Authorization": "t"}


@pytest.mark.asyncio
async def test_missing_target_reports_not_delivered():
    logger, _ = _logger()
    wh = WebhookNotifier(WebhookConfig(enabled=True, url=None, token=None, header_name="A"))
    gc = GoogleChatNotifier(GoogleChatConfig(enabled=True, webhook_url=None, min_interval_seconds=0,
                                             thread_by_check=False))
    tg = TelegramNotifier(TelegramConfig(enabled=True, bot_token=None, chat_ids=[], api_url="x",
                                         timestamp_format="%Y", timestamp_zone="UTC"))
    for channel in (wh, gc, tg):
        assert await channel.send_status_report(report=_report(), http_client=MagicMock(),
                                                logger=logger) is False


# -- scheduler ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_check_that_raises_still_lands_in_the_snapshot(monkeypatch):
    from app.core import scheduler

    class Boom:
        async def check(self, **_):
            raise RuntimeError("parser exploded")

    manager = _manager(modules=("oci",))
    module_config = MagicMock(slug="oci", interval_seconds=60)

    async def stop(_seconds):
        raise StopAfter

    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    logger, _ = _logger()
    with pytest.raises(StopAfter):
        await scheduler._run_monitor_loop(Boom(), module_config, MagicMock(), logger, manager)
    line = manager.module_reports(["oci"])[0]
    assert (line.status, line.reason) == (MonitorStatus.ERROR, "parser exploded")
    assert manager.first_round.is_set()


# -- bug-hunter --------------------------------------------------------------------


NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize("day", [datetime(2026, 3, 8, tzinfo=timezone.utc),
                                 datetime(2026, 11, 1, tzinfo=timezone.utc)])
@pytest.mark.parametrize("zone", [NEW_YORK, ZoneInfo("Europe/Lisbon")])
def test_next_occurrence_is_never_in_the_past_across_dst_days(day, zone):
    for step in range(0, 36 * 60, 5):
        now = day + timedelta(minutes=step)
        for minute in (60, 90, 120, 150):
            assert next_occurrence(now, minute, zone) > now, (now, minute, zone)


@pytest.mark.asyncio
async def test_a_restart_in_the_repeated_dst_hour_after_the_heartbeat_waits_for_tomorrow():
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=90,
                       heartbeat_timezone="America/New_York")
    restart = datetime(2026, 11, 1, 6, 20, tzinfo=timezone.utc)  # 01:20 EST, second pass
    reports = await _run_heartbeats(manager, Clock(restart), 1)
    assert reports[0].sent_at == datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_restart_in_the_spring_forward_gap_still_sends_the_days_heartbeat():
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=150,
                       heartbeat_timezone="America/New_York")
    reports = await _run_heartbeats(manager, Clock(datetime(2026, 3, 8, 7, 10, tzinfo=timezone.utc)), 1)
    assert reports[0].sent_at == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)


def _report_of(modules):
    return StatusReport(kind="heartbeat", version="2.14.0", pod="pod", channels=("telegram",),
                        started_at=T0, sent_at=T0, modules=tuple(modules), zone="UTC")


@pytest.mark.parametrize("reason", ["x" * 400, "&<>" * 130, "🔥" * 300])
def test_telegram_report_fits_the_limit_with_every_module_in_error(reason):
    modules = [ModuleReport(f"module{i:02d}", MonitorStatus.ERROR, reason, T0, T0) for i in range(20)]
    text = _render_report(_report_of(modules))
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert all(f"<b>module{i:02d}</b>" in text for i in range(20))


def test_telegram_report_keeps_full_reasons_when_they_fit():
    modules = [ModuleReport("oci", MonitorStatus.ERROR, "r" * 150, T0, T0)]
    assert "r" * 150 in _render_report(_report_of(modules))


@pytest.mark.asyncio
async def test_two_google_chat_sends_started_together_respect_the_quota():
    notifier = GoogleChatNotifier(
        GoogleChatConfig(enabled=True, webhook_url=WEBHOOK, min_interval_seconds=0.2, thread_by_check=True)
    )
    stamps = []

    async def post(url, **_):
        stamps.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.01)
        return MagicMock(status_code=200)

    client = MagicMock()
    client.post = post
    logger, _ = _logger()
    await asyncio.gather(
        notifier.send_status_report(report=_report(), http_client=client, logger=logger),
        notifier.send_alert(module_id="oci", result=_alert(), interval_seconds=60, level_name="W",
                            event_name="e", event_time=T0, http_client=client, logger=logger),
    )
    assert len(stamps) == 2 and stamps[1] - stamps[0] >= 0.19


@pytest.mark.asyncio
async def test_an_exception_building_the_report_does_not_end_the_heartbeat(monkeypatch):
    from app.core import reports

    calls = {"n": 0}
    real = reports.pod_name

    def flaky():
        calls["n"] += 1
        if calls["n"] <= REPORT_ATTEMPTS:
            raise RuntimeError("hostname lookup failed")
        return real()

    monkeypatch.setattr(reports, "pod_name", flaky)
    manager = _manager(channels={"human": Channel()}, startup_announce=False, heartbeat_minute=540)
    reports_sent = await _run_heartbeats(manager, Clock(T0 - timedelta(hours=1)), 1)
    assert [r.sent_at for r in reports_sent] == [T0 + timedelta(days=1)]
