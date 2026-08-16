"""Alert-firing simulation — proves each provider actually raises an alert.

Two simulations already exist and there is a gap between them.

`simulate_endpoints.py` proves each module *reaches* its provider and that the fields
it reads still exist there. That is the healthy path: nine modules, `status=OK`.

`simulate_notifications.py` proves the four channels receive the four events and that
a broken channel cannot silence the others — but from a single synthetic result.

Neither answers: *does the `gcp` module actually fire when GCP has an incident?*

That question is not academic. The AWS module never alerted since it was written: it
read four fields the feed does not publish and discarded every event, reporting OK with
conviction. It was caught only because AWS happened to have three live incidents during
a work session. A module can reach its provider, pass every unit test, and still never
produce an alert — because the rule, the default filter or the severity mapping is wrong
in the real configuration.

So this script takes the *real* payload of each provider, injects a degradation faithful
to that provider's shape, runs the real module, and drives the result through the real
NotificationManager to the real channels — intercepting only the outbound request.
Then it feeds the healthy payload back and checks the recovery fires.

    python scripts/simulate_alerts.py [path/to/.env]

Exit code is non-zero when a provider fails to alert, or fails to recover.

The degradations live here rather than in the modules: this is diagnostic knowledge and
production code should not carry scaffolding for it. Each one starts from the captured
payload and changes the minimum, and the report says what was changed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Callable, NamedTuple, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "tests" / "fixtures"
_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# How to break each provider, faithfully to its own shape
# ---------------------------------------------------------------------------

def _statuspage_degrade(payload):
    """Statuspage v2: flip the first component to a major outage."""
    broken = copy.deepcopy(payload)
    broken["components"][0]["status"] = "major_outage"
    return broken, f"component '{broken['components'][0]['name']}' -> major_outage"


def _cloudflare_degrade(payload):
    """Cloudflare: flip a *watched* product, not the first component.

    `_statuspage_degrade` breaks `components[0]`, which here is a continent group or a
    data center — outside the module's curated allowlist, so it would produce a
    perfectly green run that proves nothing. Tunnel is the component our own apps
    depend on, so it is the honest thing to break.
    """
    broken = copy.deepcopy(payload)
    target = next(c for c in broken["components"] if c["name"] == "Tunnel")
    target["status"] = "major_outage"
    return broken, "product 'Tunnel' -> major_outage"


def _steam_degrade(html):
    """steamstat.us: raise one service's severity class from good to major."""
    match = re.search(r'<span class="status good" id="(?!pageviews)([^"]+)">([^<]*)</span>', html)
    assert match, "no good service found in the steam page"
    broken = html.replace(
        match.group(0),
        f'<span class="status major" id="{match.group(1)}">Offline</span>',
        1,
    )
    return broken, f"service '{match.group(1)}' -> major / Offline"


def _rockstar_degrade(html):
    """Rockstar: swap the first status phrase after FiveM for a degraded one."""
    index = html.find("FiveM")
    assert index > 0, "FiveM not present in the rockstar page"
    head, tail = html[:index], html[index:]
    assert "All services operational" in tail
    return head + tail.replace("All services operational", "There are partial outages", 1), (
        "FiveM -> There are partial outages"
    )


def _oci_degrade(xml):
    """OCI RSS: reopen one incident by changing its status back to Investigating.

    The feed escapes only `<` and leaves `>` literal, so the status marker reads
    `&lt;strong>Resolved&lt;/strong>` in the raw document. ElementTree decodes it before
    the module sees it, which is why the parser matches on `<strong>` and this does not.
    """
    marker = "&lt;strong>Resolved&lt;/strong>"
    assert marker in xml, "no resolved incident to reopen"
    return xml.replace(marker, "&lt;strong>Investigating&lt;/strong>", 1), (
        "first incident Resolved -> Investigating"
    )


def _gcp_degrade(payload):
    """GCP: reopen an incident by dropping its end and restoring its locations.

    A closed incident carries its regions under `previously_affected_locations`; the
    module only looks at `currently_affected_locations`. Reopening one therefore means
    moving them back, which is exactly what GCP does when an incident is live.
    """
    broken = copy.deepcopy(payload)
    for incident in broken:
        locations = (
            incident.get("currently_affected_locations")
            or incident.get("previously_affected_locations")
        )
        if locations:
            incident.pop("end", None)
            incident["currently_affected_locations"] = locations
            return broken, (
                f"incident {incident.get('id')} -> reopened "
                f"({len(locations)} location(s) restored)"
            )
    raise AssertionError("no incident with affected locations to reopen")


def _aws_degrade(payload):
    """AWS: the current-events feed only lists open events — an empty feed is healthy.

    So the degradation is the events themselves, and the *healthy* baseline has to be
    the empty feed. Using the live payload as the baseline would start the run already
    in alert, and the injected step would be a repeat inside the throttle window rather
    than a transition.
    """
    events = payload or json.loads(
        (FIXTURES / "aws" / "current_events.json").read_text(encoding="utf-8")
    )
    source = "live" if payload else "captured"
    return events, f"{len(events)} {source} open event(s); healthy baseline is an empty feed"


def _aws_healthy(_payload):
    """An empty current-events feed: AWS reporting nothing wrong."""
    return []


class Provider(NamedTuple):
    slug: str
    fixture: Optional[str]        # relative to tests/fixtures; None = fetch live
    kind: str                     # "json" or "text"
    degrade: Callable
    rule_value: str
    service_filter: tuple = ()
    # Some providers have no healthy form of their real payload — AWS's feed only
    # lists open events — so the baseline has to be built rather than loaded.
    healthy: Optional[Callable] = None


PROVIDERS = [
    Provider("steam", "steam/all_operational.html", "text", _steam_degrade, "major,minor"),
    Provider("openai", "openai/summary.json", "json", _statuspage_degrade,
             "degraded_performance,partial_outage,major_outage"),
    Provider("claude", "claude/summary.json", "json", _statuspage_degrade,
             "degraded_performance,partial_outage,major_outage"),
    Provider("github", None, "json", _statuspage_degrade,
             "degraded_performance,partial_outage,major_outage"),
    Provider("bitbucket", None, "json", _statuspage_degrade,
             "degraded_performance,partial_outage,major_outage"),
    Provider("cloudflare", "cloudflare/summary.json", "json", _cloudflare_degrade,
             "degraded_performance,partial_outage,major_outage"),
    Provider("rockstar", "rockstar/all_operational.html", "text", _rockstar_degrade, "*"),
    Provider("oci", "oci/incident_summary.rss", "text", _oci_degrade,
             "investigating,identified,monitoring"),
    Provider("gcp", "gcp/incidents.json", "json", _gcp_degrade,
             "service_disruption,service_outage,service_information"),
    Provider("aws", None, "json", _aws_degrade, "operational_issue",
             healthy=_aws_healthy),
]

# Modules that fetch through TLS impersonation rather than the shared client.
_IMPERSONATING = {"steam", "rockstar"}


class Outcome(NamedTuple):
    slug: str
    note: str
    healthy_status: Optional[str] = None
    alert_status: Optional[str] = None
    alert_channels: int = 0
    alert_reason: str = ""
    recovery_channels: int = 0
    alert_route: str = ""
    recovery_route: str = ""
    error: Optional[str] = None

    @property
    def alerted(self) -> bool:
        return self.alert_status == "ALERT" and self.alert_channels > 0

    @property
    def recovered(self) -> bool:
        return self.recovery_channels > 0

    @property
    def route_asymmetry(self) -> bool:
        """The alert was keyed per service, but the recovery is looked up per module.

        `_extract_service_items` routes on the payload: a non-empty `list[dict]` goes to
        the per-service branch, anything else to the per-module one. A module whose
        degraded payload is a list of incidents but whose healthy payload is an empty
        list therefore records state under `<slug>:<component>` and later reads
        `<slug>` — a key that was never written. No recovery can ever fire.
        """
        return self.alert_route == PER_SERVICE and self.recovery_route == PER_MODULE


PER_SERVICE = "per-service"
PER_MODULE = "per-module"


def route_for(payload) -> str:
    """Which branch of the state machine this payload will take."""
    from app.core.notifications import _extract_service_items

    return PER_SERVICE if _extract_service_items(payload) else PER_MODULE


def verdicts(outcomes) -> list[str]:
    """Which providers failed, and why. Pure, so it is testable without the network."""
    failures = []
    for o in outcomes:
        if o.error:
            failures.append(f"{o.slug}: {o.error}")
            continue
        if not o.alerted:
            failures.append(
                f"{o.slug}: degradation produced {o.alert_status or 'nothing'}, "
                f"delivered to {o.alert_channels} channel(s)"
            )
        elif not o.recovered and o.route_asymmetry:
            failures.append(
                f"{o.slug}: alerted on {o.alert_channels} channel(s) but the all-clear can "
                f"never fire — the alert was keyed {PER_SERVICE} and the recovery is read "
                f"{PER_MODULE}, because the healthy payload is an empty list"
            )
        elif not o.recovered:
            failures.append(
                f"{o.slug}: alerted on {o.alert_channels} channel(s) but never recovered "
                f"(both phases routed {o.alert_route})"
            )
    return failures


# ---------------------------------------------------------------------------

def _load(provider, live_bodies):
    if provider.fixture:
        path = FIXTURES / provider.fixture
        text = path.read_text(encoding="utf-8")
        return (json.loads(text) if provider.kind == "json" else text), f"fixture {provider.fixture}"
    body = live_bodies[provider.slug]
    return body, "live payload"


async def _fetch_live(slug, url, client, kind):
    response = await client.get(url, timeout=25.0)
    response.raise_for_status()
    return response.json() if kind == "json" else response.text


def _module_config(provider, config_module):
    from app.core.config import ModuleConfig, RuleConfig

    return ModuleConfig(
        slug=provider.slug,
        url=config_module.url,
        interval_seconds=60,
        timeout_seconds=20.0,
        user_agent="simulate-alerts/1.0",
        rule=RuleConfig(kind="status", value=provider.rule_value),
        service_filter=list(provider.service_filter),
        enabled=True,
    )


def _notification_config():
    from app.core.config import (
        AlertmanagerConfig,
        GoogleChatConfig,
        NotificationConfig,
        TelegramConfig,
        WebhookConfig,
    )

    return NotificationConfig(
        telegram=TelegramConfig(True, "simulated", ["-100"], "https://api.telegram.org", "%Y", "UTC"),
        webhook=WebhookConfig(True, "https://hook.simulated/endpoint", None, "Authorization"),
        repeat_minutes=10,
        error_threshold=3,
        google_chat=GoogleChatConfig(
            True, "https://chat.googleapis.com/v1/spaces/S/messages?key=k&token=t", 0.0, True
        ),
        alertmanager=AlertmanagerConfig(
            True, "http://alertmanager.simulated:9093", None, "Authorization", 0.0, {}, 10
        ),
    )


def load_env_file(path: Path) -> int:
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()
        count += 1
    return count


async def main() -> int:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.config import load_app_config
    from app.core.http_client import create_http_client
    from app.core.loader import load_monitors
    from app.core.notifications import NotificationManager

    logging.disable(logging.CRITICAL)
    logger = logging.getLogger("simulate-alerts")

    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / ".env.example"
    print(f"loaded {load_env_file(env_path)} keys from {env_path}\n")

    config = load_app_config()
    monitors = {cfg.slug: (mon, cfg) for mon, cfg in load_monitors(config.modules, logger)}

    missing = [p.slug for p in PROVIDERS if p.slug not in monitors]
    if missing:
        print(f"modules not loaded: {', '.join(missing)}")
        return 1

    print(f"{len(PROVIDERS)} providers · real modules · real state machine · 4 real channels\n")

    # Anything without a fixture is read live, once.
    live_bodies = {}
    async with create_http_client(timeout_seconds=25.0, user_agent="simulate-alerts/1.0") as client:
        for provider in PROVIDERS:
            if provider.fixture:
                continue
            _mon, cfg = monitors[provider.slug]
            live_bodies[provider.slug] = await _fetch_live(
                provider.slug, cfg.url, client, provider.kind
            )

    outcomes = []
    for provider in PROVIDERS:
        monitor, module_cfg = monitors[provider.slug]
        sim_config = _module_config(provider, module_cfg)
        monitor.configure(sim_config)

        manager = NotificationManager(_notification_config())
        sent: list[str] = []

        async def capture_post(url, **_kwargs):
            sent.append(str(url).split("//")[1].split("/")[0])
            return type("R", (), {"status_code": 200, "text": "{}"})()

        client = MagicMock()
        client.post = capture_post

        try:
            loaded, source = _load(provider, live_bodies)
            broken, change = provider.degrade(loaded)
            healthy = provider.healthy(loaded) if provider.healthy else loaded
        except Exception as exc:  # noqa: BLE001
            outcomes.append(Outcome(provider.slug, "", error=f"could not degrade: {exc}"))
            continue

        async def run(payload):
            """Feed one payload through the real module."""
            if provider.slug in _IMPERSONATING:
                with patch.object(type(monitor), "_fetch_html", return_value=payload):
                    return await monitor.check(http_client=client, logger=logger)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.status_code = 200
            response.json = MagicMock(return_value=payload)
            response.text = payload if isinstance(payload, str) else ""
            client.get = AsyncMock(return_value=response)
            return await monitor.check(http_client=client, logger=logger)

        async def dispatch(result, minute):
            before = len(sent)
            await manager.handle_result(
                module_id=provider.slug,
                result=result,
                module_config=sim_config,
                level_name="WARNING",
                event_name="monitor_check",
                event_time=_T0 + timedelta(minutes=minute),
                http_client=client,
                logger=logger,
            )
            return len(sent) - before

        try:
            baseline = await run(healthy)
            await dispatch(baseline, 0)

            alert = await run(broken)
            alert_channels = await dispatch(alert, 1)

            back = await run(healthy)
            recovery_channels = await dispatch(back, 2)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(Outcome(provider.slug, change, error=f"{type(exc).__name__}: {exc}"))
            continue

        outcomes.append(
            Outcome(
                slug=provider.slug,
                note=f"{source}; {change}",
                healthy_status=baseline.status.value,
                alert_status=alert.status.value,
                alert_channels=alert_channels,
                alert_reason=(alert.reason or "")[:70],
                recovery_channels=recovery_channels,
                alert_route=route_for(alert.payload),
                recovery_route=route_for(back.payload),
            )
        )

    # -- report ------------------------------------------------------------
    header = (
        f"{'provider':<11} {'healthy':<8} {'degraded':<9} {'alert':>6} {'recov':>6}  "
        f"{'route alert->recov':<26} reason"
    )
    print(header)
    print("-" * len(header))
    for o in sorted(outcomes):
        if o.error:
            print(f"{o.slug:<11} {'ERROR':<8} {'-':<9} {'-':>6} {'-':>6}  {'-':<26} {o.error}")
            continue
        mark = "" if o.alerted and o.recovered else "   <-- CHECK"
        route = f"{o.alert_route} -> {o.recovery_route}"
        print(
            f"{o.slug:<11} {o.healthy_status:<8} {o.alert_status:<9} "
            f"{o.alert_channels:>6} {o.recovery_channels:>6}  {route:<26} {o.alert_reason}{mark}"
        )
    print("-" * len(header))

    print("\nwhat was changed, per provider")
    for o in sorted(outcomes):
        print(f"  {o.slug:<11} {o.note}")

    already_bad = [o.slug for o in outcomes if o.healthy_status == "ALERT"]
    if already_bad:
        print(
            "\nnote: these providers were already degraded before the injection, so the "
            f"baseline is not clean: {', '.join(already_bad)}"
        )

    failures = verdicts(outcomes)
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nall {len(outcomes)} providers alerted on degradation and recovered afterwards")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
