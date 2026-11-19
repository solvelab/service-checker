import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import List, Tuple

import httpx

from .config import ModuleConfig
from .notifications import NotificationManager
from .types import MonitorResult, MonitorStatus


async def schedule_monitors(
    monitors: List[Tuple[object, ModuleConfig]],
    http_client: httpx.AsyncClient,
    logger: logging.Logger,
    notifier: NotificationManager | None = None,
) -> None:
    tasks = [
        asyncio.create_task(_run_monitor_loop(monitor, config, http_client, logger, notifier))
        for monitor, config in monitors
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise


async def _run_monitor_loop(
    monitor: object,
    config: ModuleConfig,
    http_client: httpx.AsyncClient,
    logger: logging.Logger,
    notifier: NotificationManager | None,
) -> None:
    module_logger = logger.getChild(config.slug)
    # (status, reason) of the previous check: a check that repeats it is routine and
    # logs at DEBUG (#125). 119 of 121 lines in ten minutes of production were a
    # healthy check saying the same thing again, burying the two that mattered.
    previous: tuple | None = None
    while True:
        started = time.perf_counter()
        try:
            result: MonitorResult = await monitor.check(
                http_client=http_client, logger=module_logger
            )
            duration_ms = result.duration_ms
            if duration_ms is None:
                duration_ms = (time.perf_counter() - started) * 1000

            log_level = logging.INFO
            if result.status == MonitorStatus.ALERT:
                log_level = logging.WARNING
            elif result.status == MonitorStatus.ERROR:
                log_level = logging.ERROR

            # The message joins the key: two failures with the same (often empty) reason
            # are still two different failures.
            current = (result.status, result.reason, result.message)
            module_logger.log(
                logging.DEBUG if current == previous else log_level,
                result.message,
                extra={
                    "event": "monitor_check",
                    "module_id": config.slug,
                    "status": result.status.value,
                    "reason": result.reason,
                    "duration_ms": round(duration_ms, 2),
                    "interval_seconds": config.interval_seconds,
                },
            )

            previous = current
            level_name = logging.getLevelName(log_level)
            if notifier is not None:
                try:
                    await notifier.handle_result(
                        module_id=config.slug,
                        result=result,
                        module_config=config,
                        level_name=level_name,
                        event_name="monitor_check",
                        event_time=datetime.now(timezone.utc),
                        http_client=http_client,
                        logger=module_logger,
                    )
                except Exception as exc:  # noqa: BLE001
                    module_logger.error(
                        "notification dispatch failed",
                        extra={
                            "event": "notify_error",
                            "module_id": config.slug,
                            "status": MonitorStatus.ERROR.value,
                            "reason": str(exc),
                            "interval_seconds": config.interval_seconds,
                        },
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - started) * 1000
            # The exception type joins the key: str(ReadTimeout("")) is "" too, and a new
            # kind of failure must not be demoted as a repeat of the last one.
            current = (MonitorStatus.ERROR, type(exc).__name__, str(exc))
            module_logger.log(
                logging.DEBUG if current == previous else logging.ERROR,
                "monitor loop error",
                extra={
                    "event": "monitor_check",
                    "module_id": config.slug,
                    "status": MonitorStatus.ERROR.value,
                    "reason": str(exc),
                    "duration_ms": round(duration_ms, 2),
                    "interval_seconds": config.interval_seconds,
                },
            )
            previous = current
            if notifier is not None:
                # A module whose check raises still answered: the startup report shows
                # it in ERROR with the reason, instead of "no result" (#124).
                notifier.observe(
                    config.slug,
                    MonitorResult(
                        status=MonitorStatus.ERROR, message="monitor loop error", reason=str(exc)
                    ),
                    datetime.now(timezone.utc),
                )

        await asyncio.sleep(max(config.interval_seconds, 1))
