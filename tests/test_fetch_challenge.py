"""The anti-bot challenge named in the failure reason (issue #94).

Diagnosed in #87: the 403 from `steamstat.us` is Cloudflare Bot Management —
`server: cloudflare`, `cf-mitigated: challenge`, a "Just a moment..." page. The rate
moved from 63% to 0% between two days with the same profile and the same IP. It is the
source operator's policy, and "upstream returned HTTP 403" sent whoever was on call to
look at the network, the checker or the provider instead.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.impersonated_fetch import UpstreamChallenged, UpstreamRejected, fetch_html
from app.core.logging import JsonFormatter, configure_logging
from app.core.notifications import NotificationManager
from app.core.types import MonitorStatus
from app.modules.rockstar.monitor import RockstarStatusMonitor
from app.modules.steam.monitor import SteamMonitor

# Headers as observed on 2026-11-16 (cf-ray trimmed).
_CHALLENGE_HEADERS = {
    "server": "cloudflare",
    "cf-mitigated": "challenge",
    "content-type": "text/html; charset=UTF-8",
}
_CHALLENGE_BODY = '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'


class _Response:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        if headers is not None:
            self.headers = headers


def _challenge(headers=None):
    return _Response(403, _CHALLENGE_BODY, headers or dict(_CHALLENGE_HEADERS))


def _sequence(*responses):
    queue = list(responses)

    def get(url, **kwargs):
        return queue.pop(0) if queue else _Response(200, "<html>ok</html>")

    return get


def _fetch(*responses, attempts=3, logger=None):
    with patch("curl_cffi.requests.get", _sequence(*responses)):
        return fetch_html(
            "https://steamstat.us/",
            impersonate="chrome124",
            timeout_seconds=10.0,
            logger=logger or logging.getLogger("test"),
            module_id="steam",
            attempts=attempts,
            backoff_seconds=0.0,
            sleep=lambda _s: None,
        )


# ---------------------------------------------------------------------------
# Detection in the shared fetch
# ---------------------------------------------------------------------------

def test_a_challenge_raises_upstream_challenged_with_the_status():
    with pytest.raises(UpstreamChallenged) as info:
        _fetch(_challenge(), attempts=1)
    assert info.value.status == 403
    assert "challenged the automated client" in str(info.value)
    assert "Cloudflare cf-mitigated: challenge" in str(info.value)
    assert "HTTP 403" in str(info.value)


def test_a_challenge_is_still_an_upstream_rejection():
    """Callers that catch UpstreamRejected keep working."""
    with pytest.raises(UpstreamRejected):
        _fetch(_challenge(), attempts=1)


@pytest.mark.parametrize("name, value", [
    ("CF-Mitigated", "challenge"),
    ("cf-mitigated", "Challenge"),
    ("Cf-Mitigated", " challenge "),
])
def test_header_matching_ignores_case(name, value):
    with pytest.raises(UpstreamChallenged):
        _fetch(_challenge({name: value}), attempts=1)


@pytest.mark.parametrize("headers", [
    None,                                  # a response object with no headers at all
    {},
    {"server": "cloudflare"},
    {"cf-mitigated": "block"},
])
def test_a_403_without_the_challenge_header_is_the_plain_rejection(headers):
    with pytest.raises(UpstreamRejected) as info:
        _fetch(_Response(403, "denied", headers), attempts=1)
    assert not isinstance(info.value, UpstreamChallenged)
    assert str(info.value) == "upstream returned HTTP 403"


@pytest.mark.parametrize("value", ["non-challenge", "managed_challenge_failed", "challenged"])
def test_a_value_that_only_contains_challenge_is_not_a_challenge(value):
    """Equality, not substring: the reason asserts `cf-mitigated: challenge`."""
    with pytest.raises(UpstreamRejected) as info:
        _fetch(_challenge({"cf-mitigated": value}), attempts=1)
    assert not isinstance(info.value, UpstreamChallenged)


def test_a_challenge_in_a_joined_list_is_detected():
    with pytest.raises(UpstreamChallenged):
        _fetch(_challenge({"cf-mitigated": "block, challenge"}), attempts=1)


def test_duplicate_cf_mitigated_lines_from_curl_cffi_are_detected():
    """The real Headers type: repeated lines joined, keys lowercased."""
    from curl_cffi.requests.headers import Headers

    headers = Headers([b"cf-mitigated: block", b"CF-Mitigated: challenge"])
    with pytest.raises(UpstreamChallenged):
        _fetch(_challenge(headers), attempts=1)


def test_a_later_matching_key_in_a_dict_is_not_hidden_by_an_earlier_one():
    with pytest.raises(UpstreamChallenged):
        _fetch(_challenge({"cf-mitigated": "block", "CF-Mitigated": "challenge"}), attempts=1)


@pytest.mark.parametrize("headers", [[("cf-mitigated", "challenge")], "cf-mitigated: challenge"])
def test_headers_without_items_keep_the_plain_rejection(headers):
    with pytest.raises(UpstreamRejected) as info:
        _fetch(_Response(404, "x", headers), attempts=3)
    assert str(info.value) == "upstream returned HTTP 404"


def _counting(*responses):
    calls = []
    inner = _sequence(*responses)

    def get(url, **kwargs):
        calls.append(url)
        return inner(url, **kwargs)

    return get, calls


def _fetch_counted(*responses):
    get, calls = _counting(*responses)
    with patch("curl_cffi.requests.get", get):
        try:
            fetch_html("https://steamstat.us/", impersonate="chrome124", timeout_seconds=10.0,
                       logger=logging.getLogger("test"), module_id="steam", attempts=3,
                       backoff_seconds=0.0, sleep=lambda _s: None)
        except UpstreamRejected as exc:
            return exc, len(calls)
    raise AssertionError("expected a rejection")


class _BrokenHeaders:
    def items(self):
        raise RuntimeError("header decode failed")


def _capture_logger():
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger, stream


def test_a_header_read_failure_keeps_the_plain_404_and_logs_it():
    """Issue #98: detection only adds to the reason, it never changes the rejection."""
    logger, stream = _capture_logger()
    get, calls = _counting(*[_Response(404, "x", _BrokenHeaders())] * 3)
    with patch("curl_cffi.requests.get", get), pytest.raises(UpstreamRejected) as info:
        fetch_html("https://steamstat.us/", impersonate="chrome124", timeout_seconds=10.0,
                   logger=logger, module_id="steam", attempts=3,
                   backoff_seconds=0.0, sleep=lambda _s: None)
    assert type(info.value) is UpstreamRejected
    assert str(info.value) == "upstream returned HTTP 404"
    assert info.value.status == 404
    assert len(calls) == 1
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    failed = [line for line in lines if line.get("event") == "challenge_check_failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "WARNING"
    assert failed[0]["module_id"] == "steam"
    assert failed[0]["status"] == 404
    assert "RuntimeError: header decode failed" in failed[0]["reason"]


def test_a_header_read_failure_on_a_403_is_still_retried():
    exc, calls = _fetch_counted(*[_Response(403, "x", _BrokenHeaders())] * 3)
    assert type(exc) is UpstreamRejected
    assert str(exc) == "upstream returned HTTP 403"
    assert calls == 3


class _HugeError:
    def items(self):
        raise ValueError("x" * 10_000)


def test_the_logged_header_error_is_bounded():
    logger, stream = _capture_logger()
    with pytest.raises(UpstreamRejected):
        _fetch(_Response(404, "x", _HugeError()), attempts=1, logger=logger)
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    failed = [line for line in lines if line.get("event") == "challenge_check_failed"]
    assert len(failed) == 1
    assert len(failed[0]["reason"]) <= 200


@pytest.mark.parametrize("headers", [None, {}])
def test_absent_headers_are_not_a_header_read_failure(headers):
    logger, stream = _capture_logger()
    with pytest.raises(UpstreamRejected):
        _fetch(_Response(403, "x", headers), attempts=1, logger=logger)
    assert "challenge_check_failed" not in stream.getvalue()


def test_the_header_guard_is_checked_on_its_own_by_the_silent_fallback_detector():
    """The guard's clause must not share the allowlisted text of fetch_html's loop."""
    from tests.test_silent_fallbacks import ACCEPTED_SILENT, _except_blocks

    guards = [b for b in _except_blocks()
              if b["file"] == "core/impersonated_fetch.py" and "header_error" in b["clause"]]
    assert len(guards) == 1
    assert guards[0]["logs"]
    assert (guards[0]["file"], guards[0]["clause"]) not in ACCEPTED_SILENT


def test_a_challenge_with_503_is_retried_and_named():
    exc, calls = _fetch_counted(*[_Response(503, "x", {"cf-mitigated": "challenge"})] * 3)
    assert isinstance(exc, UpstreamChallenged)
    assert exc.status == 503 and "HTTP 503" in str(exc)
    assert calls == 3


@pytest.mark.parametrize("status", [401, 404, 520])
def test_a_challenge_outside_the_retryable_set_is_not_retried(status):
    """Retry follows the status, as documented in the README and the docstring."""
    exc, calls = _fetch_counted(*[_Response(status, "x", {"cf-mitigated": "challenge"})] * 3)
    assert isinstance(exc, UpstreamChallenged)
    assert calls == 1


def test_the_header_on_a_success_is_not_a_failure():
    assert _fetch(_Response(200, "<html>ok</html>", dict(_CHALLENGE_HEADERS))) == "<html>ok</html>"


def test_a_challenge_is_retried_and_can_pass():
    """Part of the challenge clears on the next attempt (#87)."""
    assert _fetch(_challenge(), _Response(200, "<html>ok</html>")) == "<html>ok</html>"


def test_the_retry_log_names_the_challenge():
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    with pytest.raises(UpstreamChallenged):
        _fetch(_challenge(), _challenge(), _challenge(), logger=logger)

    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    retries = [line for line in lines if line.get("event") == "fetch_retry"]
    assert len(retries) == 2
    assert all("challenged the automated client" in line["reason"] for line in retries)


# ---------------------------------------------------------------------------
# Through the modules
# ---------------------------------------------------------------------------

def _module_config(slug, url, service_filter):
    return ModuleConfig(slug, url, 60, 10.0, "ua", RuleConfig("status", "major"),
                        service_filter, True)


@pytest.mark.parametrize("monitor_cls, slug, url, service_filter", [
    (SteamMonitor, "steam", "https://steamstat.us/", []),
    (RockstarStatusMonitor, "rockstar", "https://support.rockstargames.com/servicestatus",
     ["FiveM"]),
])
def test_the_module_error_reason_names_the_challenge(monitor_cls, slug, url, service_filter):
    monitor = monitor_cls()
    monitor.configure(_module_config(slug, url, service_filter))
    monitor._backoff = 0.0
    with patch("curl_cffi.requests.get", _sequence(_challenge(), _challenge(), _challenge())):
        result = asyncio.run(
            monitor.check(http_client=None, logger=logging.getLogger("test"))
        )
    assert result.status == MonitorStatus.ERROR
    assert result.reason.startswith("UpstreamChallenged: upstream challenged the automated client")


# ---------------------------------------------------------------------------
# Into the notification
# ---------------------------------------------------------------------------

class _Spy:
    def __init__(self):
        self.monitor_errors: list[str] = []

    async def send_alert(self, **kwargs):
        return True

    async def send_recovery(self, **kwargs):
        return True

    async def send_monitor_error(self, **kwargs):
        self.monitor_errors.append(kwargs["result"].reason or "")
        return True

    async def send_monitor_recovered(self, **kwargs):
        return True


def test_the_monitoring_failure_notification_carries_the_challenge():
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=10,
            error_threshold=3,
        )
    )
    spy = _Spy()
    manager.register("spy", spy)
    monitor = SteamMonitor()
    config = _module_config("steam", "https://steamstat.us/", [])
    monitor.configure(config)
    monitor._backoff = 0.0
    t0 = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)

    async def scenario():
        for minute in range(3):
            with patch("curl_cffi.requests.get",
                       _sequence(_challenge(), _challenge(), _challenge())):
                result = await monitor.check(http_client=None, logger=logging.getLogger("t"))
            await manager.handle_result(
                module_id="steam",
                result=result,
                module_config=config,
                level_name="ERROR",
                event_name="monitor_check",
                event_time=t0 + timedelta(minutes=minute),
                http_client=AsyncMock(),
                logger=MagicMock(spec=logging.Logger),
            )

    asyncio.run(scenario())
    assert len(spy.monitor_errors) == 1
    assert "last error: UpstreamChallenged: upstream challenged the automated client" in (
        spy.monitor_errors[0]
    )
