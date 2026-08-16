"""Notification delivery simulation — proves every channel gets every event.

`simulate_endpoints.py` covers the left half of the pipeline: can each module still
read its provider. This covers the right half: once a module reports something, does
every configured channel actually receive it, and does the rendered message reach the
wire.

It builds a real NotificationManager with the real Telegram and webhook notifiers,
real templates and the real state machine, and intercepts only the final HTTP POST.
Nothing is mocked above the transport, so a regression anywhere between
`handle_result` and the request body shows up here.

A deliberately broken channel is registered alongside the real ones. Channels are
supposed to be isolated: one failing must not stop the others. That used to be false.

    python scripts/simulate_notifications.py

Exit code is non-zero when any channel misses any event, or when the broken channel
manages to suppress a healthy one.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import (  # noqa: E402
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import NotificationManager  # noqa: E402
from app.core.types import NOTIFIER_METHODS, MonitorResult, MonitorStatus  # noqa: E402

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
_THRESHOLD = 3

EVENT_LABELS = {
    "send_alert": "service alert",
    "send_recovery": "service recovery",
    "send_monitor_error": "monitoring failure",
    "send_monitor_recovered": "monitoring recovery",
}


class CapturingClient:
    """Stands in for the shared httpx client, recording what each channel posts."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))

        class _Response:
            status_code = 200
            text = "{}"

        return _Response()


class BrokenChannel:
    """A channel that fails on every event, the way a revoked token would."""

    def __init__(self) -> None:
        self.attempts = 0

    async def _fail(self, **kwargs):
        self.attempts += 1
        raise RuntimeError("simulated channel outage")

    send_alert = _fail
    send_recovery = _fail
    send_monitor_error = _fail
    send_monitor_recovered = _fail


class WitnessChannel:
    """A complete channel that only records, to observe dispatch directly."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def _record(self, name):
        async def handler(**kwargs):
            self.events.append(name)

        return handler

    def __getattr__(self, name):
        if name in NOTIFIER_METHODS:
            return self._record(name)
        raise AttributeError(name)


def _module_config() -> ModuleConfig:
    return ModuleConfig(
        slug="rockstar",
        url="https://support.rockstargames.com/servicestatus",
        interval_seconds=60,
        timeout_seconds=15.0,
        user_agent="simulate/1.0",
        rule=RuleConfig(kind="status", value="major"),
        service_filter=[],
        enabled=True,
    )


def _notification_config() -> NotificationConfig:
    return NotificationConfig(
        telegram=TelegramConfig(
            enabled=True,
            bot_token="simulated-token",
            chat_ids=["-1001234567890"],
            api_url="https://api.telegram.org",
            timestamp_format="%Y-%m-%d %H:%M:%S %Z",
            timestamp_zone="UTC",
        ),
        webhook=WebhookConfig(
            enabled=True,
            url="https://hook.example.com/service-checker",
            token="simulated-secret",
            header_name="Authorization",
        ),
        repeat_minutes=10,
        error_threshold=_THRESHOLD,
    )


def _alert() -> MonitorResult:
    return MonitorResult(
        status=MonitorStatus.ALERT,
        message="rockstar status degraded",
        reason="FiveM: There are partial outages, RedM: All services down",
        reason_items=["FiveM: There are partial outages", "RedM: All services down"],
        duration_ms=142.0,
        payload={"hero": "Some services degraded", "services": []},
    )


def _error() -> MonitorResult:
    return MonitorResult(
        status=MonitorStatus.ERROR,
        message="rockstar status request failed",
        reason="OSError: Name or service not known",
        duration_ms=51.0,
        payload=None,
    )


def _ok() -> MonitorResult:
    return MonitorResult(
        status=MonitorStatus.OK,
        message="rockstar status healthy",
        duration_ms=88.0,
        payload={"hero": "All services operational", "services": []},
    )


def _channel_of(url: str) -> str:
    if "api.telegram.org" in url:
        return "telegram"
    if "hook.example.com" in url:
        return "webhook"
    return url


def _describe(kwargs: dict) -> str:
    body: Any = kwargs.get("json") or {}
    if "text" in body:  # telegram
        first = body["text"].splitlines()[0]
        return first.replace("<b>", "").replace("</b>", "")
    return f"status={body.get('status')} event={body.get('event')}"


async def main() -> int:
    logging.disable(logging.CRITICAL)

    manager = NotificationManager(_notification_config())
    witness = WitnessChannel()
    broken = BrokenChannel()
    manager.register("witness", witness)
    manager.register("broken", broken)

    client = CapturingClient()
    logger = logging.getLogger("simulate")
    module_config = _module_config()

    async def feed(result, minute):
        before = len(client.posts)
        await manager.handle_result(
            module_id="rockstar",
            result=result,
            module_config=module_config,
            level_name="WARNING",
            event_name="monitor_check",
            event_time=_T0 + timedelta(minutes=minute),
            http_client=client,
            logger=logger,
        )
        return client.posts[before:]

    print("channels registered: telegram, webhook, witness, broken (always raises)\n")
    print("driving one full lifecycle through the real state machine:\n")

    timeline = [
        ("ALERT", _alert(), 0),
        ("ERROR", _error(), 1),
        ("ERROR", _error(), 2),
        ("ERROR", _error(), 3),  # threshold reached here
        ("OK", _ok(), 4),
    ]

    for label, result, minute in timeline:
        sent = await feed(result, minute)
        note = "" if sent else "  (no notification — expected below threshold)"
        print(f"  t+{minute}min  {label:<6}{note}")
        for url, kwargs in sent:
            print(f"           -> {_channel_of(url):<9} {_describe(kwargs)}")
    print()

    # --- verdicts -----------------------------------------------------------
    failures: list[str] = []

    posted_by_channel: dict[str, int] = {}
    for url, _ in client.posts:
        channel = _channel_of(url)
        posted_by_channel[channel] = posted_by_channel.get(channel, 0) + 1

    print("delivery matrix")
    print("-" * 72)
    print(f"  {'channel':<12} {'events':>7}  verdict")
    for channel in ("telegram", "webhook"):
        count = posted_by_channel.get(channel, 0)
        ok = count == 4
        print(f"  {channel:<12} {count:>7}  {'ok' if ok else 'MISSING EVENTS'}")
        if not ok:
            failures.append(f"{channel} received {count} of 4 expected events")

    seen = set(witness.events)
    missing = [m for m in NOTIFIER_METHODS if m not in seen]
    print(f"  {'witness':<12} {len(witness.events):>7}  "
          f"{'ok' if not missing else 'MISSING: ' + ', '.join(missing)}")
    if missing:
        failures.append(f"witness never received: {', '.join(missing)}")

    print(f"  {'broken':<12} {broken.attempts:>7}  raised every time, by design")
    if broken.attempts == 0:
        failures.append("the broken channel was never called — dispatch skipped it")

    print("-" * 72)
    print("\nevent coverage")
    for method in NOTIFIER_METHODS:
        hit = method in seen
        print(f"  {EVENT_LABELS[method]:<22} {'delivered' if hit else 'NEVER FIRED'}")

    print("\nisolation")
    isolated = not missing and posted_by_channel.get("telegram", 0) == 4
    print(f"  a channel that raises on every event {'did not' if isolated else 'DID'} "
          f"suppress the healthy ones")
    if not isolated:
        failures.append("a failing channel suppressed a healthy one")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nall channels received all four events; a broken channel changed nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
