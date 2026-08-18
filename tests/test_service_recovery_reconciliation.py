"""Tests for recovering components that drop out of the payload.

Every module follows the same contract, and it is the contract that makes this
necessary: an ALERT payload carries **only** the items that still match the rule
(`_evaluate_status_rule` returns `matches`), while an OK payload carries the full set.
A component therefore announces its recovery by *leaving* the payload, not by appearing
in it as healthy.

The state machine used to reconcile against presence instead of against the cycle's item
set, and two things followed from that:

- `aws` and `gcp` publish only open events, so their healthy payload is `[]`. That is
  falsy, so the result routed to the per-module branch and read `<module_id>` — a key the
  per-service alert never wrote. Both alerted on every channel and could never emit the
  all-clear. The alert-firing simulation is what surfaced it.
- The same defect hid in every other module in its partial form: three components
  degraded and one recovering leaves two in the payload, and the third never cleared.

`test_simulate_alerts.py` covers the report that found this; this file covers the fix.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus

_T0 = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class Spy:
    def __init__(self):
        self.alerts: list[str] = []
        self.recoveries: list[str] = []

    async def send_alert(self, **kwargs):
        self.alerts.append(kwargs["result"].reason or "")
        return True

    async def send_recovery(self, **kwargs):
        self.recoveries.append(kwargs["result"].reason or "")
        return True

    async def send_monitor_error(self, **kwargs):
        return True

    async def send_monitor_recovered(self, **kwargs):
        return True


def _manager(repeat_minutes=10):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=repeat_minutes,
            error_threshold=3,
        )
    )
    spy = Spy()
    manager.register("spy", spy)
    return manager, spy


def _module_config(slug="aws"):
    return ModuleConfig(
        slug=slug,
        url="https://example.com",
        interval_seconds=60,
        timeout_seconds=10.0,
        user_agent="test/1.0",
        rule=RuleConfig(kind="status", value="open"),
        service_filter=[],
        enabled=True,
    )


async def _feed(manager, status, payload, minute, module_id="aws"):
    reason = "degraded" if status == MonitorStatus.ALERT else None
    await manager.handle_result(
        module_id=module_id,
        result=MonitorResult(status, "m", reason, 10.0, payload),
        module_config=_module_config(module_id),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=AsyncMock(),
        logger=MagicMock(spec=logging.Logger),
    )


def _c(cid, name, status="open"):
    return {"id": cid, "name": name, "status": status}


# ---------------------------------------------------------------------------
# The defect: an empty healthy payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_empty_healthy_payload_recovers_the_component_that_alerted():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("dx-eu", "AWS Direct Connect")], 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert len(spy.alerts) == 1
    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_the_all_clear_names_the_component_not_just_the_module():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("dx-eu", "AWS Direct Connect")], 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert "AWS Direct Connect" in spy.recoveries[0]


@pytest.mark.asyncio
async def test_every_component_of_an_emptied_payload_recovers():
    """AWS had three open events at once; all three must clear."""
    manager, spy = _manager()
    events = [_c("e1", "Direct Connect"), _c("e2", "EC2"), _c("e3", "S3")]
    await _feed(manager, MonitorStatus.ALERT, events, 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert len(spy.alerts) == 3
    assert len(spy.recoveries) == 3
    assert {"Direct Connect", "EC2", "S3"} == {r.split(":")[0] for r in spy.recoveries}


@pytest.mark.asyncio
async def test_the_key_is_gone_after_the_all_clear():
    """Left behind, one key per component would grow for the process's whole life."""
    manager, _ = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("dx-eu", "Direct Connect")], 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert manager._alert_state.get("aws:dx-eu") is None


@pytest.mark.asyncio
async def test_a_component_that_stays_absent_does_not_recover_again():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("dx-eu", "Direct Connect")], 0)
    for minute in (1, 2, 30, 600):
        await _feed(manager, MonitorStatus.OK, [], minute)

    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_a_healthy_module_that_never_alerted_stays_quiet():
    manager, spy = _manager()
    for minute in range(4):
        await _feed(manager, MonitorStatus.OK, [], minute)

    assert spy.alerts == [] and spy.recoveries == []


# ---------------------------------------------------------------------------
# Partial recovery — the form the defect took in every other module
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_of_two_recovering_clears_only_that_one():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "Compute"), _c("b", "Storage")], 0)
    # Storage still matches the rule, so the ALERT payload carries only Storage.
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Storage")], 1)

    assert len(spy.recoveries) == 1
    assert "Compute" in spy.recoveries[0]


@pytest.mark.asyncio
async def test_the_component_still_degraded_is_not_re_alerted_by_the_reconciliation():
    """It is inside the throttle window; recovering its neighbour must not reset that."""
    manager, spy = _manager(repeat_minutes=10)
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "Compute"), _c("b", "Storage")], 0)
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Storage")], 1)

    assert len(spy.alerts) == 2  # the two from minute 0, none added


@pytest.mark.asyncio
async def test_the_component_still_degraded_keeps_repeating_on_its_own_schedule():
    manager, spy = _manager(repeat_minutes=10)
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "Compute"), _c("b", "Storage")], 0)
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Storage")], 1)
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Storage")], 15)

    assert len(spy.alerts) == 3
    assert "Storage" in spy.alerts[-1]


@pytest.mark.asyncio
async def test_recovering_one_does_not_clear_a_component_still_in_the_payload():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "Compute"), _c("b", "Storage")], 0)
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Storage")], 1)
    await _feed(manager, MonitorStatus.OK, [], 2)

    # Compute cleared at minute 1, Storage at minute 2 — one each, never twice.
    assert len(spy.recoveries) == 2


# ---------------------------------------------------------------------------
# Bug-Hunter — hostile shapes and sequences
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_flapping_feed_reports_every_transition_it_is_shown():
    """A feed that intermittently omits an item produces alert/recover/alert.

    Asserted deliberately rather than debounced: each notification is faithful to the
    evidence of its cycle, and inventing a "wait N cycles" rule would suppress a real
    short outage. If a provider is ever seen dropping items it still considers open,
    that is the moment to add debouncing — with the provider named.
    """
    manager, spy = _manager()
    for minute, payload in enumerate([[_c("a", "X")], [], [_c("a", "X")], []]):
        status = MonitorStatus.ALERT if payload else MonitorStatus.OK
        await _feed(manager, status, payload, minute)

    assert len(spy.alerts) == 2
    assert len(spy.recoveries) == 2


@pytest.mark.asyncio
async def test_a_none_payload_recovers_the_pending_components():
    """`None` is as falsy as `[]`; three gcp branches return it."""
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "X")], 0)
    await _feed(manager, MonitorStatus.OK, None, 1)

    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_a_component_changing_its_id_recovers_the_old_and_alerts_the_new():
    """The key is the identity; a changed id is a different component, not the same one."""
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("old", "Compute")], 0)
    await _feed(manager, MonitorStatus.ALERT, [_c("new", "Compute")], 1)

    assert len(spy.alerts) == 2
    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_two_components_sharing_a_name_are_reconciled_independently():
    manager, spy = _manager()
    both = [_c("a", "Compute"), _c("b", "Compute")]
    await _feed(manager, MonitorStatus.ALERT, both, 0)
    await _feed(manager, MonitorStatus.ALERT, [_c("b", "Compute")], 1)

    assert len(spy.alerts) == 2
    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_components_without_ids_are_reconciled_by_their_content_digest():
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [{"detail": "region down"}], 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert len(spy.alerts) == 1
    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_a_module_id_that_prefixes_another_is_not_reconciled_by_it():
    """`git` must not sweep the state of `github`; the separator is what protects it."""
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("x", "Git Ops")], 0, module_id="github")
    await _feed(manager, MonitorStatus.OK, [], 1, module_id="git")

    assert spy.recoveries == []
    assert manager._alert_state.get("github:x") is not None


@pytest.mark.asyncio
async def test_a_per_module_provider_is_untouched_by_the_reconciliation():
    """rockstar's payload is a dict in both phases and writes no `<slug>:` key."""
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, {"hero": "degraded"}, 0, module_id="rockstar")
    await _feed(manager, MonitorStatus.OK, {"hero": "ok"}, 1, module_id="rockstar")

    assert len(spy.alerts) == 1
    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_an_error_between_alert_and_recovery_does_not_lose_the_all_clear():
    """ERROR is about the monitor, so it must not consume the component's pending state."""
    manager, spy = _manager()
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "X")], 0)
    await _feed(manager, MonitorStatus.ERROR, None, 1)
    await _feed(manager, MonitorStatus.OK, [], 2)

    assert len(spy.recoveries) == 1


@pytest.mark.asyncio
async def test_a_broken_channel_does_not_stop_the_reconciliation():
    manager, spy = _manager()

    class Exploding:
        async def send_alert(self, **kwargs):
            raise RuntimeError("channel is broken")

        async def send_recovery(self, **kwargs):
            raise RuntimeError("channel is broken")

        async def send_monitor_error(self, **kwargs): ...

        async def send_monitor_recovered(self, **kwargs): ...

    manager.register("broken", Exploding())
    await _feed(manager, MonitorStatus.ALERT, [_c("a", "X")], 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert len(spy.recoveries) == 1
    assert manager._alert_state.get("aws:a") is None


@pytest.mark.asyncio
async def test_many_components_clearing_at_once_all_notify():
    """A burst: a provider closing a large incident drops every event in one cycle."""
    manager, spy = _manager()
    events = [_c(f"e{i}", f"Service {i}") for i in range(120)]
    await _feed(manager, MonitorStatus.ALERT, events, 0)
    await _feed(manager, MonitorStatus.OK, [], 1)

    assert len(spy.alerts) == 120
    assert len(spy.recoveries) == 120
    assert not [k for k in manager._alert_state if k.startswith("aws:")]
