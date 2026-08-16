import asyncio

from ._version import __version__
from .core.config import AppConfig, drain_config_warnings, load_app_config
from .core.http_client import create_http_client
from .core.loader import load_monitors
from .core.logging import configure_logging
from .core.notifications import NotificationManager
from .core.scheduler import schedule_monitors


async def run_monitor_service() -> None:
    config: AppConfig = load_app_config()
    logger = configure_logging(config.log_level)

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
        },
    )

    if not config.modules:
        logger.warning("no modules configured; exiting", extra={"event": "startup"})
        return

    monitors = load_monitors(config.modules, logger)
    if not monitors:
        logger.error("failed to load any modules; exiting", extra={"event": "startup"})
        return

    notifier = NotificationManager(config.notifications)

    async with create_http_client(
        timeout_seconds=config.defaults.timeout_seconds,
        user_agent=config.defaults.user_agent,
    ) as http_client:
        await schedule_monitors(monitors, http_client, logger, notifier)


def main() -> None:
    asyncio.run(run_monitor_service())


if __name__ == "__main__":
    main()
