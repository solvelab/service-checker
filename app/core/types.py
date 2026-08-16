from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional


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

