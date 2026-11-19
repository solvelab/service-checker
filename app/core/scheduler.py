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

            module_logger.log(
                log_level,
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
            module_logger.error(
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
