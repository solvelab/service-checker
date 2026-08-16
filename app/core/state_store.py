"""Alert state that survives a restart.

`_alert_state` and `_error_state` live in a dict on the process, so a restart wipes
both. The consequence is not cosmetic: an incident that spans a restart **never gets its
all-clear**. The operator is told "Tunnel: major_outage" and, if the pod restarts before
the provider recovers, the "resolved" message simply never arrives. From the outside that
is indistinguishable from an incident still in progress.

No provider announces twice. If the degradation ended while the process was down, the
payload comes back healthy, the state is empty, and there is no transition to notify —
the same failure mode the vanished-component reconciliation fixed, arriving by a
different road.

This is deliberately a plain JSON file and not a database: the daemon has no database,
no HTTP server, and one replica. A file it writes atomically and re-reads on boot is the
smallest thing that closes the gap.

Two rules shape everything here:

- **Never break the monitor.** Every failure — unreadable file, bad JSON, wrong schema,
  no write permission, full disk — is logged and swallowed. A monitor that will not start
  because it could not persist bookkeeping is worse than one that forgets.
- **Only pending alerts are worth keeping.** An OK entry is bookkeeping that the state
  machine drops anyway, and an alert old enough to be stale should not produce a
  surprise all-clear days later.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from .types import MonitorStatus

SCHEMA_VERSION = 1

#: A timestamp this far ahead of now is not a clock skew, it is a corrupt or
#: hand-edited file. Keeping it would freeze the repeat throttle forever, because the
#: elapsed time never reaches the repeat window.
_FUTURE_TOLERANCE = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class StateStore:
    """Reads and writes the notification state as one JSON document.

    A store with no path is a no-op that always loads empty and never writes — that is
    the default, so a deployment that does not mount a volume behaves exactly as before.
    """

    def __init__(
        self,
        path: Optional[str],
        max_age_minutes: int = 1440,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_age = timedelta(minutes=max(max_age_minutes, 0))
        self._logger = logger or logging.getLogger(__name__)
        # What was last written, so an unchanged state costs nothing. The common case
        # is every provider healthy and the document identical cycle after cycle;
        # without this the daemon would fsync ten times a minute to say nothing.
        self._last_written: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    # -- load ---------------------------------------------------------------

    def load(self, now: Optional[datetime] = None) -> Tuple[dict, dict]:
        """Return `(alert_state, error_state)`, or two empty dicts on any problem."""
        from .notifications import AlertState, MonitorErrorState

        if self.path is None or not self.path.exists():
            return {}, {}

        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._warn("state file could not be read; starting with empty state", exc)
            return {}, {}

        if not isinstance(document, dict):
            self._warn("state file is not an object; starting with empty state", None)
            return {}, {}

        version = document.get("version")
        if version != SCHEMA_VERSION:
            # Not an error: a future version may legitimately not be readable by this
            # one. Forgetting is the safe direction.
            self._warn(f"state file schema is {version!r}, expected {SCHEMA_VERSION}", None)
            return {}, {}

        now = now or _now()
        alerts: dict = {}
        dropped = 0
        for key, raw in (document.get("alerts") or {}).items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                dropped += 1
                continue
            when = _parse_dt(raw.get("last_alert_at"))
            if when is None:
                dropped += 1
                continue
            if when > now + _FUTURE_TOLERANCE:
                dropped += 1
                continue
            if self.max_age and now - when > self.max_age:
                dropped += 1
                continue
            item = raw.get("last_item")
            alerts[key] = AlertState(
                last_status=MonitorStatus.ALERT,
                last_alert_at=when,
                last_status_text=raw.get("last_status_text") or None,
                last_item=item if isinstance(item, dict) else None,
            )

        errors: dict = {}
        for key, raw in (document.get("errors") or {}).items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                dropped += 1
                continue
            count = raw.get("consecutive_errors")
            errors[key] = MonitorErrorState(
                consecutive_errors=count if isinstance(count, int) and count >= 0 else 0,
                last_notified_at=_parse_dt(raw.get("last_notified_at")),
                last_reason=raw.get("last_reason") or None,
            )

        self._logger.info(
            "notification state restored",
            extra={
                "event": "state_load",
                "target": str(self.path),
                "reason": (
                    f"{len(alerts)} pending alert(s), {len(errors)} monitor error(s), "
                    f"{dropped} entry(ies) discarded"
                ),
            },
        )
        return alerts, errors

    # -- save ---------------------------------------------------------------

    def save(self, alert_state: dict, error_state: dict) -> None:
        """Write the pending state atomically. Any failure is logged, never raised."""
        if self.path is None:
            return

        document = {
            "version": SCHEMA_VERSION,
            "alerts": {
                key: {
                    "last_alert_at": state.last_alert_at.isoformat(),
                    "last_status_text": state.last_status_text,
                    "last_item": state.last_item,
                }
                # Only pending alerts. An OK entry is transient bookkeeping, and
                # persisting it would resurrect keys the state machine just dropped.
                for key, state in alert_state.items()
                if state.last_status == MonitorStatus.ALERT and state.last_alert_at
            },
            "errors": {
                key: {
                    "consecutive_errors": state.consecutive_errors,
                    "last_notified_at": (
                        state.last_notified_at.isoformat() if state.last_notified_at else None
                    ),
                    "last_reason": state.last_reason,
                }
                for key, state in error_state.items()
            },
        }

        payload = json.dumps(document, ensure_ascii=False, default=str, sort_keys=True)
        if payload == self._last_written:
            return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Written to a sibling temp file and renamed, so a crash mid-write leaves the
            # previous state intact instead of a truncated document.
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            self._last_written = payload
        except Exception as exc:  # noqa: BLE001
            self._warn("state file could not be written", exc)

    # -- helpers ------------------------------------------------------------

    def _warn(self, message: str, exc: Optional[Exception]) -> None:
        self._logger.warning(
            message,
            extra={
                "event": "state_error",
                "target": str(self.path),
                "reason": f"{type(exc).__name__}: {exc}" if exc else message,
            },
        )
