"""Status board in the log (#125).

There is no dashboard, so `kubectl logs` is the panel. One `status_board` event lists
every provider with its status, since when, the last check and a note — the current
reason, or the last error once the provider is healthy again, so an error that scrolled
away is still on the board. It goes out after the first round, after every status
change (close changes coalesce into one board) and every N minutes, so the tail of the
log always holds a recent one.

The event carries plain data; `PrettyFormatter` draws it as a table, `JsonFormatter`
writes it as one parseable object.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Iterable, Optional

from .reports import format_uptime
from .types import ModuleReport, MonitorStatus

#: Changes inside this window are drawn as one board: modules finish their cycles a few
#: seconds apart, and a router hiccup would otherwise print one board per module.
COALESCE_SECONDS = 5


def _iso(at: Optional[datetime]) -> Optional[str]:
    return at.isoformat() if at is not None else None


def _note(line: ModuleReport) -> Optional[str]:
    if line.status is not None and line.status != MonitorStatus.OK:
        return line.reason
    if line.last_error_at is not None:
        return f"last error {line.last_error_at:%d/%m %H:%MZ}: {line.last_error or 'unknown'}"
    return None


def build_board(
    manager, module_ids: Iterable[str], started_at: datetime, version: str, now: datetime
) -> dict:
    lines = manager.module_reports(module_ids)
    return {
        "version": version,
        "uptime": format_uptime(now - started_at),
        "providers": [
            {
                "module": line.module_id,
                "status": line.status.value if line.status is not None else None,
                "since": _iso(line.since),
                "checked_at": _iso(line.checked_at),
                "note": _note(line),
            }
            for line in lines
        ],
        "channels": list(manager.channel_names()),
        "open_alerts": manager.open_alerts(),
        "failing": sum(line.status == MonitorStatus.ERROR for line in lines),
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def emit_board(manager, module_ids, started_at, version, logger, now) -> None:
    logger.info(
        "status board",
        extra={
            "event": "status_board",
            "board": build_board(manager, module_ids, started_at, version, now),
        },
    )


async def run_status_board(
    manager,
    module_ids: Iterable[str],
    logger: logging.Logger,
    *,
    version: str,
    minutes: float,
    startup_timeout_seconds: float,
    clock: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Draw the board after the first round, on every change and every `minutes`."""
    module_ids = tuple(module_ids)
    started_at = clock()
    try:
        await asyncio.wait_for(manager.first_round.wait(), timeout=startup_timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "status board drawn before every module answered",
            extra={"event": "status_board_partial", "timeout_seconds": startup_timeout_seconds},
        )
    manager.status_changed.clear()
    emit_board(manager, module_ids, started_at, version, logger, clock())

    period = minutes * 60 if minutes > 0 else None
    while True:
        # A timeout is the periodic board: nothing changed, and that is worth saying too.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(manager.status_changed.wait(), timeout=period)
            await sleep(COALESCE_SECONDS)
        manager.status_changed.clear()
        emit_board(manager, module_ids, started_at, version, logger, clock())
