from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional, Protocol, runtime_checkable

import httpx


class MonitorStatus(str, Enum):
    OK = "OK"
    ALERT = "ALERT"
    ERROR = "ERROR"


@dataclass
class MonitorResult:
    status: MonitorStatus
    message: str
    reason: Optional[str] = None
    duration_ms: Optional[float] = None
    payload: Optional[Any] = None
    # One entry per incident, already separated by the monitor that found them.
    # `reason` is the same content joined into a sentence and stays the wire format
    # for logs and the webhook; this list is what renders as bullets. Splitting the
    # joined string back apart cannot work: every separator collides with content
    # from some provider (OCI titles contain "|", GitHub incident titles contain ",").
    reason_items: Optional[List[str]] = None



# Every notification channel implements exactly these four methods, with these
# keyword arguments. The contract used to be implicit — whatever NotificationManager
# happened to call — so a channel missing a method only failed at the first event of
# that type in production. NotificationManager validates the four names at
# registration time against this tuple.
NOTIFIER_METHODS = (
    "send_alert",
    "send_recovery",
    "send_monitor_error",
    "send_monitor_recovered",
)


@runtime_checkable
class Notifier(Protocol):
    """A notification channel.

    `send_alert` and `send_recovery` are about the monitored service; the two
    `send_monitor_*` are about the checker's own ability to evaluate it. A channel
    must keep them distinguishable — "the service is down" and "I cannot check"
    are different pages for whoever is on call.

    Implementations own their transport failures: an HTTP error is logged and
    swallowed, never raised. NotificationManager isolates channels from each other
    anyway, so a channel that does raise cannot silence the others.
    """

    async def send_alert(
        self,
        *,
        module_id: str,
        result: "MonitorResult",
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None: ...

    async def send_recovery(
        self,
        *,
        module_id: str,
        result: "MonitorResult",
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None: ...

    async def send_monitor_error(
        self,
        *,
        module_id: str,
        result: "MonitorResult",
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None: ...

    async def send_monitor_recovered(
        self,
        *,
        module_id: str,
        result: "MonitorResult",
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None: ...
