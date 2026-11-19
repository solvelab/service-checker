"""Liveness file for the Kubernetes probe (#134).

The app serves no HTTP, so the kubelet reads a file instead: `app.healthcheck` fails
when its mtime gets old. A dead process already restarts the container; this covers the
one that stays alive and stuck — a blocked event loop, or a module whose loop hangs.
The file is touched only while every module keeps completing checks, so a hung module
stops it as surely as a blocked loop does.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable, Iterable, Optional

from .config import ModuleConfig
from .impersonated_fetch import DEFAULT_ATTEMPTS, DEFAULT_BACKOFF_SECONDS, worst_case_seconds

#: How often the file is touched. The probe's max age (default 120 s) must exceed it.
TOUCH_SECONDS = 30

#: Missed cycles before a module counts as stuck: one slow cycle is not a hang.
CYCLES = 3

#: Floor of every window. A cycle also waits for notification delivery, serialized
#: across modules under the channels' locks (10 s per post), and rockstar/steam may set
#: more fetch attempts than the default. A restart does not fix a slow Telegram and
#: stops all monitoring during CrashLoop backoff, so a hang is caught late rather than
#: a slow channel killing the pod.
MIN_WINDOW_SECONDS = 900


def window_seconds(config: ModuleConfig) -> float:
    """How long a module may go without completing a check before it counts as stuck."""
    window = CYCLES * max(config.interval_seconds, 1) + worst_case_seconds(
        config.timeout_seconds, DEFAULT_ATTEMPTS, DEFAULT_BACKOFF_SECONDS
    )
    # A nan or inf timeout would make every comparison false: never stale.
    return max(window, MIN_WINDOW_SECONDS) if math.isfinite(window) else MIN_WINDOW_SECONDS


def stale_modules(
    manager, configs: Iterable[ModuleConfig], started_at: datetime, now: datetime
) -> list:
    """Modules past their window; one not checked yet counts from the process start."""
    configs = list(configs)
    reports = manager.module_reports([config.slug for config in configs])
    return [
        config.slug
        for config, report in zip(configs, reports)
        if (now - (report.checked_at or started_at)).total_seconds() > window_seconds(config)
    ]


def touch(path: str) -> None:
    with open(path, "a"):
        pass
    os.utime(path)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_liveness(
    manager,
    configs: Iterable[ModuleConfig],
    path: Optional[str],
    logger: logging.Logger,
    *,
    clock: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Touch `path` every TOUCH_SECONDS while every module is on time; log transitions."""
    if not path:
        return
    configs = tuple(configs)
    started_at = clock()
    stale: list = []
    write_error: Optional[str] = None
    check_error: Optional[str] = None
    while True:
        try:
            current = stale_modules(manager, configs, started_at, clock())
        except Exception as exc:  # noqa: BLE001
            # A bug here is not a stuck monitor: restarting would loop on the same bug and
            # stop monitoring. The file keeps going; a blocked loop still stops it.
            error = f"{type(exc).__name__}: {exc}"
            if error != check_error:
                logger.error(
                    "liveness check failed; file still written",
                    extra={"event": "liveness_check_failed", "reason": error},
                )
            check_error = error
            current = []
        else:
            check_error = None
        if current != stale:
            if current:
                logger.warning(
                    "liveness file not updated: modules overdue",
                    extra={"event": "liveness_stale", "modules": current},
                )
            else:
                logger.info("liveness file updated again", extra={"event": "liveness_fresh"})
            stale = current
        if not current:
            try:
                touch(path)
            except OSError as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error != write_error:  # once per distinct failure, not every 30 s
                    logger.error(
                        "liveness file write failed",
                        extra={"event": "liveness_write_failed", "reason": error},
                    )
                write_error = error
            else:
                if write_error is not None:
                    # Only after a failure: the first success is silent, the startup log
                    # already names the path.
                    logger.info(
                        "liveness file written again", extra={"event": "liveness_write_recovered"}
                    )
                write_error = None
        await sleep(TOUCH_SECONDS)
