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
    # The ERROR is the edge's anti-bot challenge (`UpstreamChallenged`), the source
    # operator's policy rather than a fault of the provider; the manager tolerates a
    # streak made only of these for longer (#112).
    challenged: bool = False



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

    Swallowing is not the same as hiding. Every method returns whether at least one
    target accepted the message: `True` when someone received it, `False` when nobody
    did. NotificationManager only advances the alert state on `True`, so a channel
    that reports `False` gets the event retried on the next cycle instead of having it
    suppressed by the repeat throttle. A channel that swallowed the failure *and*
    returned `True` would put the state past an alert no one ever saw — the defect
    this contract exists to prevent.

    A channel that sends to several targets returns `True` when any of them accepted:
    someone read it, and resending would duplicate the message for them.
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
    ) -> bool: ...

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
    ) -> bool: ...

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
    ) -> bool: ...

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
    ) -> bool: ...


#: The two status reports a human channel receives (#124).
REPORT_KINDS = ("startup", "heartbeat")


@dataclass(frozen=True)
class ModuleReport:
    """One provider's line in a status report.

    `status` is None for a module that has not delivered a result yet: the startup
    announcement does not wait forever for a slow module, and says so instead.
    """

    module_id: str
    status: Optional[MonitorStatus]
    reason: Optional[str] = None
    checked_at: Optional[datetime] = None
    since: Optional[datetime] = None


@dataclass(frozen=True)
class StatusReport:
    """What the checker says about itself: "I am up" or "I am still up" (#124).

    The alert channels say what is wrong with a provider; this says the checker runs,
    delivers, and reads every configured provider. Silence on a channel used to mean
    either "all healthy" or "the checker is dead", with no way to tell them apart.
    """

    kind: str
    version: str
    pod: str
    channels: tuple
    started_at: datetime
    sent_at: datetime
    modules: tuple
    # IANA zone the channels show times in (`NOTIFICATION_HEARTBEAT_TIMEZONE`).
    zone: str = "UTC"


@runtime_checkable
class StatusReporter(Protocol):
    """A channel that delivers the status reports to a human.

    Kept apart from `Notifier` on purpose: Alertmanager is a record, not a delivery to
    anyone, and a "checker is up" alert there would be noise or, worse, a fake
    delivery. The human channels implement it; `tests/test_status_reports.py` fails
    when one of them stops doing so, so a silent channel is caught before production.
    Returns whether at least one target accepted, like every `Notifier` method.
    """

    async def send_status_report(
        self,
        *,
        report: StatusReport,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool: ...
