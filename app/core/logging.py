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
    # Connectivity loss settings as read (#106, #108): the operator checks the windows here.
    "connectivity",
    "challenge",
    # Status report settings as read (#124).
    "reports",
    "timeout_seconds",
    # The log's status board (#125).
    "board",
    # The file the Kubernetes probe reads (#134).
    "liveness_path",
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


#: Keys `PrettyFormatter` shows elsewhere on the line, or not at all.
_PRETTY_SKIP = ("event", "module_id", "board")
_PRETTY_WIDTH = 100


class PrettyFormatter(logging.Formatter):
    """One short line per event, and a table for the status board (#125).

    For `kubectl logs` read by a person. Same allowlist as `JsonFormatter`, so nothing
    reaches this format that the JSON one would have kept out — credentials included.
    Times are UTC, marked `Z`, like the JSON timestamps.
    """

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%SZ")
        level = record.levelname[:5]
        module = getattr(record, "module_id", None) or "-"
        head = f"{stamp}  {level:<5}  {module:<11}  {_one_line(record.getMessage())}"
        board = getattr(record, "board", None)
        if getattr(record, "event", None) == "status_board" and isinstance(board, dict):
            text = _render_board(stamp, level, board)
        else:
            fields = [
                f"{key}={_pretty_value(getattr(record, key))}"
                for key in _EXTRA_KEYS
                if key not in _PRETTY_SKIP and getattr(record, key, None) is not None
            ]
            text = "  ".join([head, *fields]) if fields else head
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def _one_line(text: str) -> str:
    """Escape control characters: an upstream `\\n` or ESC must not forge a log line."""
    if text.isprintable():
        return text
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _pretty_value(value: Any) -> str:
    if isinstance(value, str):
        if value and value.isprintable() and " " not in value:
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clock(iso: Any, fmt: str) -> str:
    if not isinstance(iso, str):
        return "—"
    try:
        return datetime.datetime.fromisoformat(iso).astimezone(datetime.timezone.utc).strftime(fmt)
    except ValueError:
        return "—"


def _render_board(stamp: str, level: str, board: Dict[str, Any]) -> str:
    title = _one_line(f"── status board ─ v{board.get('version', '?')} ─ up {board.get('uptime', '?')} ")
    lines = [f"{stamp}  {level:<5}  {title}".ljust(_PRETTY_WIDTH, "─")[:_PRETTY_WIDTH]]
    header = f"  {'PROVIDER':<12}{'STATUS':<8}{'SINCE':<13}{'CHECKED':<10}NOTE"
    lines.append(header)
    note_width = _PRETTY_WIDTH - len(header) + len("NOTE")
    for row in board.get("providers") or []:
        note = " ".join(str(row.get("note") or "—").split())
        if len(note) > note_width:
            note = note[: note_width - 1] + "…"
        lines.append(
            f"  {str(row.get('module'))[:11]:<12}"
            f"{str(row.get('status') or '—'):<8}"
            f"{_clock(row.get('since'), '%d/%m %H:%M'):<13}"
            f"{_clock(row.get('checked_at'), '%H:%M:%S'):<10}"
            f"{note}"
        )
    channels = _one_line(", ".join(board.get("channels") or []) or "none")
    lines.append(
        f"  channels: {channels}   open alerts: {board.get('open_alerts', 0)}"
        f"   failing: {board.get('failing', 0)}"
    )
    return "\n".join(lines)


def configure_logging(level_name: str, log_format: str = "json") -> logging.Logger:
    logger = logging.getLogger("service_monitor")
    logger.setLevel(_coerce_level(level_name))
    logger.propagate = False

    # Recreate handlers for idempotency.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PrettyFormatter() if log_format == "pretty" else JsonFormatter())
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
