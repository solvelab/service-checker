"""Status reports: the startup announcement and the daily heartbeat (#124).

The alert channels only speak when something breaks, so silence used to mean either
"every provider is healthy" or "the checker is not running, or cannot deliver". The
startup announcement says the pod is up, which channels deliver, and what each
configured provider answered on its first check — a wrong `SERVICE_FILTER` shows up
here as an ERROR with its reason, instead of waiting for the failure threshold. The
heartbeat says, once a day, that the checker is still running: a heartbeat that does
not arrive is the signal.

The rendering helpers live here, not in the channels, so the three channels cannot
disagree on what a status or an uptime means.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from .types import ModuleReport, MonitorStatus, StatusReport

#: Attempts per report when no channel accepts it, and the pause between them.
REPORT_ATTEMPTS = 3
REPORT_RETRY_SECONDS = 60

TITLES = {
    "startup": "✅ Service checker online",
    "heartbeat": "💓 Service checker still running",
}
SUBTITLES = {
    "startup": "First check of every configured provider",
    "heartbeat": "Daily heartbeat — no message at this time means the checker is down",
}
STATUS_ICONS = {
    MonitorStatus.OK: "🟢",
    MonitorStatus.ALERT: "🟠",
    MonitorStatus.ERROR: "🔴",
    None: "⚪",
}
NO_RESULT = "no result yet"


#: A reason longer than this is cut: twelve providers with incident titles would
#: otherwise pass Telegram's 4096-character limit and the whole report would bounce.
REASON_LIMIT = 200


def short_reason(reason: Optional[str], limit: int = REASON_LIMIT) -> str:
    text = " ".join((reason or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def status_label(line: ModuleReport) -> str:
    return line.status.value if line.status is not None else NO_RESULT


def format_uptime(delta: timedelta) -> str:
    """`3d 4h 12m`, `4h 12m` or `12m`; never negative."""
    minutes = max(int(delta.total_seconds()) // 60, 0)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def summary_counts(report: StatusReport) -> str:
    """`5 OK · 1 ALERT` style tally, in a fixed order, skipping zeros."""
    order = (MonitorStatus.OK, MonitorStatus.ALERT, MonitorStatus.ERROR, None)
    counts = {key: 0 for key in order}
    for line in report.modules:
        counts[line.status] = counts.get(line.status, 0) + 1
    parts = [
        f"{counts[key]} {key.value if key is not None else 'no result'}"
        for key in order
        if counts[key]
    ]
    return " · ".join(parts) or "no providers"


def local_time(at: Optional[datetime], zone: str, fmt: str = "%d/%m %H:%M") -> str:
    """`at` shown in `zone`; an empty string for a missing time."""
    if at is None:
        return ""
    # The config validated the zone; a bad one from a hand-built config raises here
    # and `_dispatch` logs it per channel, which is louder than a silent UTC.
    return at.astimezone(ZoneInfo(zone)).strftime(fmt)


def pod_name() -> str:
    return os.environ.get("HOSTNAME") or socket.gethostname() or "unknown"


def next_occurrence(now: datetime, minute_of_day: int, zone: ZoneInfo) -> datetime:
    """The first `HH:MM` in `zone` strictly after `now`, as an aware datetime.

    Strictly after is what keeps a restart from repeating the day's heartbeat: a pod
    that comes back at 09:00:20, after sending at 09:00:00, waits for tomorrow.
    """
    local = now.astimezone(zone)
    at = time(minute_of_day // 60, minute_of_day % 60)
    # Compared as instants, never as wall clocks: two datetimes sharing one ZoneInfo
    # compare by wall time and ignore the offset, so in the repeated hour of a DST
    # fall-back a 01:30 already sent looked "still ahead" and went out twice, and in
    # the skipped hour of a spring-forward the day's heartbeat was dropped (bug-hunter).
    for day in (local.date(), local.date() + timedelta(days=1)):
        candidate = datetime.combine(day, at, tzinfo=zone).astimezone(timezone.utc)
        if candidate > now:
            return candidate
    return datetime.combine(local.date() + timedelta(days=2), at, tzinfo=zone).astimezone(
        timezone.utc
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_status_reports(
    manager,
    config,
    module_ids: Iterable[str],
    http_client,
    logger: logging.Logger,
    *,
    version: str,
    clock: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Send the startup announcement, then a heartbeat every day, until cancelled."""
    module_ids = tuple(module_ids)
    started_at = clock()
    if not manager.report_channels():
        if config.startup_announce or config.heartbeat_minute is not None:
            logger.warning(
                "no channel delivers status reports; startup and heartbeat are off",
                extra={"event": "status_report_skip", "reason": "no human channel enabled"},
            )
        return

    if config.startup_announce:
        try:
            await asyncio.wait_for(
                manager.first_round.wait(), timeout=config.startup_timeout_seconds
            )
        except asyncio.TimeoutError:
            missing = [
                line.module_id for line in manager.module_reports(module_ids)
                if line.status is None
            ]
            logger.warning(
                "startup report sent before every module answered",
                extra={
                    "event": "status_report_partial",
                    "modules": missing,
                    "timeout_seconds": config.startup_timeout_seconds,
                },
            )
        await _send(manager, "startup", module_ids, started_at, http_client, logger,
                    version=version, clock=clock, sleep=sleep)

    if config.heartbeat_minute is None:
        return
    zone = ZoneInfo(config.heartbeat_timezone)
    while True:
        try:
            target = next_occurrence(clock(), config.heartbeat_minute, zone)
            # A sleep that wakes early would send before the hour and then pick the
            # same hour again; loop until the clock has really passed it.
            while (remaining := (target - clock()).total_seconds()) > 0:
                await sleep(remaining)
            await _send(manager, "heartbeat", module_ids, started_at, http_client, logger,
                        version=version, clock=clock, sleep=sleep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # One bad day must not end the heartbeat for the pod's whole life: its
            # absence is read as "the checker is down" (bug-hunter).
            logger.error(
                "heartbeat cycle failed",
                extra={"event": "status_report_failed", "reason": f"{type(exc).__name__}: {exc}"},
            )
            await sleep(REPORT_RETRY_SECONDS)


async def _send(
    manager,
    kind: str,
    module_ids: tuple,
    started_at: datetime,
    http_client,
    logger: logging.Logger,
    *,
    version: str,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
) -> bool:
    event = "startup_announced" if kind == "startup" else "heartbeat_sent"
    zone = getattr(manager.config, "heartbeat_timezone", "UTC")
    for attempt in range(1, REPORT_ATTEMPTS + 1):
        report = None
        try:
            report = build_report(
                manager, kind, module_ids, started_at, version, clock(), zone=zone
            )
            delivered = await manager.send_status_report(report, http_client, logger)
        except Exception as exc:  # noqa: BLE001 - a report must never stop the checker
            delivered = False
            logger.error(
                "status report dispatch failed",
                extra={"event": "notify_error", "reason": f"{type(exc).__name__}: {exc}"},
            )
        if delivered and report is not None:
            logger.info(
                f"{kind} report delivered",
                extra={
                    "event": event,
                    "reason": summary_counts(report),
                    "modules": [line.module_id for line in report.modules],
                },
            )
            return True
        if attempt < REPORT_ATTEMPTS:
            await sleep(REPORT_RETRY_SECONDS)
    logger.error(
        f"{kind} report not delivered",
        extra={
            "event": "status_report_failed",
            "reason": f"no channel accepted it after {REPORT_ATTEMPTS} attempts",
        },
    )
    return False


def build_report(
    manager,
    kind: str,
    module_ids: tuple,
    started_at: datetime,
    version: str,
    now: Optional[datetime] = None,
    *,
    zone: str = "UTC",
) -> StatusReport:
    return StatusReport(
        kind=kind,
        version=version,
        pod=pod_name(),
        channels=manager.channel_names(),
        started_at=started_at,
        sent_at=now or _utcnow(),
        modules=manager.module_reports(module_ids),
        zone=zone,
    )
