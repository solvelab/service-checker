import datetime
import json
import logging
import sys
from typing import Any, Dict


#: Extras que o formatter deixa passar. É uma allowlist de propósito: um `extra`
#: arbitrário poderia arrastar credencial para o log. Mas a lista precisa acompanhar o
#: código — uma chave usada e não listada **some em silêncio**, e foi assim que
#: `"notification channel failed"` passou a não dizer qual canal falhou.
#: `tests/test_logging.py::test_every_extra_key_used_in_the_app_is_allowed` quebra
#: quando alguém adiciona uma chave nova sem passar por aqui.
_EXTRA_KEYS = (
    "event",
    "version",
    "module_id",
    "status",
    "reason",
    "duration_ms",
    "interval_seconds",
    "check_id",
    "from_status",
    "to_status",
    "target",
    "url",
    "chat_id",
    "space",
    "modules",
    "defaults",
    "timeout_seconds",
    "user_agent",
)


#: Mesmo motivo do buffer em `config.py`: o aviso nasce enquanto o logger ainda
#: esta sendo montado, entao e emitido logo depois, por `configure_logging`.
_LEVEL_WARNINGS: list = []


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log: Dict[str, Any] = {
            "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
        }

        for key in _EXTRA_KEYS:
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

    while _LEVEL_WARNINGS:
        logger.warning(
            _LEVEL_WARNINGS.pop(0), extra={"event": "config_fallback"}
        )

    return logger


def _coerce_level(level_name: str) -> int:
    """Traduz o nome do nivel, avisando quando o nome nao existe.

    Silenciosamente virar INFO fazia `SERVICE_MONITOR_LOG_LEVEL=DEBUGG` parecer aceito.
    O aviso sai pelo logger recem-configurado, entao ele proprio respeita o nivel que
    acabou de ser escolhido — e por isso e WARNING, que sobrevive a INFO.
    """
    resolved = getattr(logging, str(level_name).upper(), None)
    if isinstance(resolved, int):
        return resolved
    _LEVEL_WARNINGS.append(
        f"SERVICE_MONITOR_LOG_LEVEL={level_name!r} is not a log level; using INFO"
    )
    return logging.INFO
