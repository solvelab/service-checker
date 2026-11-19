import asyncio

from ._version import __version__
from .core.config import AppConfig, drain_config_warnings, load_app_config
from .core.http_client import create_http_client
from .core.loader import load_monitors
from .core.logging import configure_logging
from .core.notifications import NotificationManager
from .core.board import run_status_board
from .core.liveness import run_liveness
from .core.reports import run_status_reports
from .core.scheduler import schedule_monitors


async def run_monitor_service() -> None:
    config: AppConfig = load_app_config()
    logger = configure_logging(config.log_level, config.log_format)

    # A configuracao e lida antes de o logger existir, entao os avisos dela ficam
    # guardados e saem agora. Sem isto, um valor recusado — `INTERVAL_SECONDS=6O`, com a
    # letra O — vira o default em silencio, e o operador segue acreditando que
    # configurou o que digitou.
    for warning in drain_config_warnings():
        logger.warning(warning, extra={"event": "config_fallback"})

    logger.info(
        "service monitor starting",
        extra={
            "event": "startup",
            "version": __version__,
            "modules": [module.slug for module in config.modules],
            "defaults": {
                "interval_seconds": config.defaults.interval_seconds,
                "timeout_seconds": config.defaults.timeout_seconds,
                "user_agent": config.defaults.user_agent,
            },
            "connectivity": {
                "quorum": config.notifications.connectivity_quorum,
                "tolerance_minutes": config.notifications.connectivity_tolerance_minutes,
                "windows": [
                    f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
                    for start, end in config.notifications.connectivity_windows
                ],
                "timezone": config.notifications.connectivity_timezone,
            },
            "challenge": {
                "tolerance_minutes": config.notifications.challenge_tolerance_minutes,
            },
            # Status report settings as read (#124): a heartbeat time that fell back to
            # the default after a typo is visible here, like the connectivity windows.
            # The file the Kubernetes probe reads (#134); null means off.
            "liveness_path": config.liveness_path,
            "reports": {
                "startup_announce": config.notifications.startup_announce,
                "startup_timeout_seconds": config.notifications.startup_timeout_seconds,
                "heartbeat_time": (
                    None
                    if config.notifications.heartbeat_minute is None
                    else f"{config.notifications.heartbeat_minute // 60:02d}:"
                    f"{config.notifications.heartbeat_minute % 60:02d}"
                ),
                "heartbeat_timezone": config.notifications.heartbeat_timezone,
            },
        },
    )

    if not config.modules:
        logger.warning("no modules configured; exiting", extra={"event": "startup"})
        return

    monitors = load_monitors(config.modules, logger)
    if not monitors:
        logger.error("failed to load any modules; exiting", extra={"event": "startup"})
        return

    notifier = NotificationManager(
        config.notifications, module_ids=[module_config.slug for _, module_config in monitors]
    )

    async with create_http_client(
        timeout_seconds=config.defaults.timeout_seconds,
        user_agent=config.defaults.user_agent,
    ) as http_client:
        module_ids = [module_config.slug for _, module_config in monitors]
        side_tasks = [
            asyncio.create_task(
                _guard(
                    run_status_reports(
                        notifier,
                        config.notifications,
                        module_ids,
                        http_client,
                        logger,
                        version=__version__,
                    ),
                    logger,
                    "status reports stopped",
                    "status_report_failed",
                )
            ),
            asyncio.create_task(
                _guard(
                    run_status_board(
                        notifier,
                        module_ids,
                        logger,
                        version=__version__,
                        minutes=config.status_board_minutes,
                        startup_timeout_seconds=config.notifications.startup_timeout_seconds,
                    ),
                    logger,
                    "status board stopped",
                    "status_board_failed",
                )
            ),
            asyncio.create_task(
                _guard(
                    run_liveness(
                        notifier,
                        [module_config for _, module_config in monitors],
                        config.liveness_path,
                        logger,
                    ),
                    logger,
                    "liveness stopped",
                    "liveness_failed",
                )
            ),
        ]
        try:
            await schedule_monitors(monitors, http_client, logger, notifier)
        finally:
            for task in side_tasks:
                task.cancel()


async def _guard(work, logger, message: str, event: str) -> None:
    """A bug in a side task (reports, board) must be loud and never stop the monitoring.

    A task that dies with an exception nobody awaits vanishes without a line; the
    heartbeat would then stop as silently as the dead pod it exists to reveal.
    """
    try:
        await work
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(message, extra={"event": event, "reason": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    asyncio.run(run_monitor_service())


if __name__ == "__main__":
    main()
