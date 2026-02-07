import datetime
import json
import logging
import sys
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log: Dict[str, Any] = {
            "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
        }

        for key in (
            "event",
            "module_id",
            "status",
            "reason",
            "duration_ms",
            "interval_seconds",
            "check_id",
            "from_status",
            "to_status",
        ):
            value = getattr(record, key, None)
            if value is not None:
                log[key] = value

        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)

        return json.dumps(log, ensure_ascii=True)


def configure_logging(level_name: str) -> logging.Logger:
    logger = logging.getLogger("service_monitor")
    logger.setLevel(_coerce_level(level_name))
    logger.propagate = False

    # Recreate handlers for idempotency.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    return logger


def _coerce_level(level_name: str) -> int:
    try:
        return getattr(logging, level_name.upper())
    except AttributeError:
        return logging.INFO
