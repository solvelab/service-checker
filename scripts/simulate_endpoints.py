"""Live endpoint simulation — proves each module can still read its upstream.

The unit suite runs against frozen fixtures, so it proves the parser handles the
payload of the day it was captured. It cannot tell you that a provider renamed its
fields last month and the module has been silently blind ever since. That happened:
the AWS module read four fields that did not exist and discarded every event, while
reporting OK with total confidence.

This script closes that gap. For each configured module it runs one real check and,
where the upstream shape is known, checks that the fields the module depends on are
actually present in the payload.

    python scripts/simulate_endpoints.py [path/to/.env]

This queries every configured provider for real, so it is a diagnostic, **not a deterministic
gate**.
A momentary hiccup on any of them is indistinguishable, in a single run, from a module
that is genuinely broken — and the difference is the whole point: the AWS blindness was
permanent and reproducible, a network timeout is not.

So a module that fails is retried before the script calls it a failure. A failure that
does not repeat is reported as transient and does not fail the run; one that repeats
does. Set `SIMULATE_ATTEMPTS=1` to disable the retry and restore the previous behaviour.

Do not wire this into CI expecting a stable signal: a transient upstream will still be
reported, just not as a failure, and a provider that is flaky for minutes will still go
red.

The field expectations live here rather than in the modules on purpose: this is a
diagnostic tool, and production code should not carry scaffolding for it.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Fields each module reads from every upstream record. A field missing from 100% of
# records means the module is parsing a payload shape that no longer exists.
CONTRACTS: dict[str, dict] = {
    "aws": {"kind": "json-list", "record_fields": ["arn", "service", "status", "summary"]},
    "gcp": {"kind": "json-list", "record_fields": ["id", "status_impact"]},
    "oci": {"kind": "rss", "record_tag": "item", "record_fields": ["title", "description"]},
    "github": {"kind": "json-collection", "collection": "components", "record_fields": ["id", "name", "status"]},
    "bitbucket": {"kind": "json-collection", "collection": "components", "record_fields": ["id", "name", "status"]},
    "openai": {"kind": "json-collection", "collection": "components", "record_fields": ["id", "name", "status"]},
    "claude": {"kind": "json-collection", "collection": "components", "record_fields": ["id", "name", "status"]},
    "cloudflare": {"kind": "json-collection", "collection": "components", "record_fields": ["id", "name", "status"]},
    # steam and rockstar parse HTML behind TLS impersonation; the module's own parse
    # count is the only meaningful signal, so they carry no field contract.
}


def _attempts() -> int:
    """How many times a failing module is tried before the run calls it a failure."""
    try:
        return max(int(os.getenv("SIMULATE_ATTEMPTS", "2")), 1)
    except ValueError:
        return 2


def _retry_delay() -> float:
    """Pause between attempts, so a struggling provider is not hammered."""
    try:
        return max(float(os.getenv("SIMULATE_RETRY_DELAY", "2.0")), 0.0)
    except ValueError:
        return 2.0


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


def extract_records(slug: str, response) -> tuple[list[dict], Optional[str]]:
    """Pull the upstream's records out of an already-fetched response.

    Takes the response rather than a URL on purpose: the raw count and the field
    check must describe the *same* read as the module evaluated. Fetching again
    would show a different snapshot whenever an incident opens or closes in
    between, and the `raw` and `parsed` columns would stop being comparable —
    which is the one thing an operator reads them for.
    """
    contract = CONTRACTS[slug]
    kind = contract["kind"]
    if kind == "rss":
        return [
            dict.fromkeys(re.findall(r"<(\w+)[ >]", chunk))
            for chunk in re.findall(r"<item>(.*?)</item>", response.text, re.S)
        ], None
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return [], "body is not JSON"
    if kind == "json-collection":
        data = data.get(contract["collection"], []) if isinstance(data, dict) else []
    if not isinstance(data, list):
        return [], None
    return [r for r in data if isinstance(r, dict)], None


def check_contract(slug: str, records: list[dict]) -> tuple[int, list[str], str]:
    """Return (record count, fields absent from every record, note)."""
    if not records:
        return 0, [], "upstream reports no records"
    missing = [
        field
        for field in CONTRACTS[slug]["record_fields"]
        if not any(field in record for record in records)
    ]
    return len(records), missing, ""


class RecordingClient:
    """The shared client, remembering the last response per URL.

    The module's own check already fetched the upstream; asking for it a second time
    doubles the load on every third-party service and, worse, reads a different
    snapshot. When an incident opens or closes between the two reads, the `raw` and
    `parsed` columns describe different moments — and comparing those two columns is
    the one thing an operator reads them for.
    """

    def __init__(self, client) -> None:
        self._client = client
        self.responses: dict[str, Any] = {}

    async def get(self, url, **kwargs):
        response = await self._client.get(url, **kwargs)
        self.responses[str(url)] = response
        return response

    async def post(self, url, **kwargs):
        return await self._client.post(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


async def inspect_upstream(slug: str, url: str, client) -> tuple[int, list[str], str]:
    """Report the contract against the response the module already read.

    Falls back to fetching only when the module never reached that URL — it failed
    before the request, or it uses its own transport (the TLS-impersonating modules
    do, and they carry no contract anyway).
    """
    if slug not in CONTRACTS:
        return -1, [], "no contract (HTML)"

    response = getattr(client, "responses", {}).get(str(url))
    if response is None:
        try:
            response = await client.get(url, timeout=20.0)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return -1, [], f"fetch failed: {type(exc).__name__}"

    records, note = extract_records(slug, response)
    if note:
        return -1, [], note
    return check_contract(slug, records)


def _count_parsed(payload) -> int:
    """How many components the module actually recognised.

    A dict payload is a module-level result (rockstar); its component list lives
    under "services". Counting the dict's keys instead would report a fixed 3.
    """
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        services = payload.get("services")
        return len(services) if isinstance(services, list) else 0
    return 0


async def resolve_with_retry(slugs, runner, attempts, delay, sleep=None):
    """Separate a provider hiccup from a module that is genuinely broken.

    `runner(slug)` is awaited and returns `(ok, detail)`. Only slugs that failed are
    retried, so a healthy module is never queried twice.

    Returns `(transient, persistent)`, both mapping slug to the detail of the *first*
    failure. A transient failure is still named in the report — silencing it would hide
    a provider that is flaky every few minutes, which is real information.
    """
    sleeper = sleep or asyncio.sleep
    first_detail = dict(slugs)
    pending = list(first_detail)
    for _ in range(max(attempts, 1) - 1):
        if not pending:
            break
        await sleeper(delay)
        still_failing = []
        for slug in pending:
            ok, _detail = await runner(slug)
            if not ok:
                still_failing.append(slug)
        pending = still_failing
    persistent = {slug: first_detail[slug] for slug in pending}
    transient = {
        slug: detail for slug, detail in first_detail.items() if slug not in persistent
    }
    return transient, persistent


async def main() -> int:
    from app.core.config import load_app_config
    from app.core.http_client import create_http_client
    from app.core.loader import load_monitors
    from app.core.logging import configure_logging

    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / ".env.example"
    print(f"loaded {load_env_file(env_path)} keys from {env_path}\n")

    config = load_app_config()
    logger = configure_logging("CRITICAL")

    requested = [m.slug for m in config.modules]
    monitors = load_monitors(config.modules, logger)
    loaded = [c.slug for _, c in monitors]
    missing_modules = [s for s in requested if s not in loaded]

    print(f"requested ({len(requested)}): {', '.join(requested)}")
    print(f"loaded    ({len(loaded)}): {', '.join(loaded)}")
    if missing_modules:
        print(f"FAILED TO LOAD: {', '.join(missing_modules)}")
    print()

    failures: dict[str, str] = {s: "module failed to load" for s in missing_modules}
    by_slug = {cfg.slug: (monitor, cfg) for monitor, cfg in monitors}

    async with create_http_client(
        timeout_seconds=config.defaults.timeout_seconds,
        user_agent=config.defaults.user_agent,
    ) as raw_client:
        client = RecordingClient(raw_client)

        async def run_one(monitor, cfg):
            started = time.perf_counter()
            try:
                result = await monitor.check(
                    http_client=client, logger=logger.getChild(cfg.slug)
                )
                raised = None
            except Exception as exc:  # noqa: BLE001
                result, raised = None, f"{type(exc).__name__}: {exc}"
            elapsed = (time.perf_counter() - started) * 1000
            records, missing, note = await inspect_upstream(cfg.slug, cfg.url, client)
            return cfg.slug, result, raised, elapsed, records, missing, note

        results = await asyncio.gather(*(run_one(m, c) for m, c in monitors))

    print(f"{'module':<11} {'status':<7} {'ms':>7} {'raw':>5} {'parsed':>7}  contract")
    print("-" * 96)
    for slug, result, raised, elapsed, records, missing, note in sorted(results):
        if raised is not None:
            print(f"{slug:<11} {'RAISED':<7} {elapsed:>7.0f} {'-':>5} {'-':>7}  {raised}")
            failures[slug] = f"uncaught exception: {raised}"
            continue

        parsed = _count_parsed(result.payload)
        raw = "-" if records < 0 else str(records)
        verdict = note or "ok"
        if missing:
            verdict = f"BLIND — fields absent upstream: {', '.join(missing)}"
            failures[slug] = verdict
        print(
            f"{slug:<11} {result.status.value:<7} {elapsed:>7.0f} {raw:>5} {parsed:>7}  {verdict}"
        )
        if result.reason:
            print(f"{'':<11} {'':<7} {'':>7} {'':>5} {'':>7}  reason: {result.reason[:80]}")
        if result.status.value == "ERROR":
            failures[slug] = result.reason or result.message

    print("-" * 96)

    attempts = _attempts()
    transient: dict[str, str] = {}
    persistent = dict(failures)
    retryable = {s: w for s, w in failures.items() if s not in missing_modules}

    if retryable and attempts > 1:
        print(f"\n{len(retryable)} module(s) failed; retrying only those "
              f"({attempts - 1} more attempt(s)) to tell a provider hiccup "
              f"from a broken module\n")

        async with create_http_client(
            timeout_seconds=config.defaults.timeout_seconds,
            user_agent=config.defaults.user_agent,
        ) as raw_retry_client:
            # The retry deliberately reads again — a fresh read is the whole point there.
            client = RecordingClient(raw_retry_client)

            async def retry_one(slug):
                monitor, cfg = by_slug[slug]
                try:
                    result = await monitor.check(
                        http_client=client, logger=logger.getChild(slug)
                    )
                except Exception as exc:  # noqa: BLE001
                    return False, f"{type(exc).__name__}: {exc}"
                if result.status.value == "ERROR":
                    return False, result.reason or result.message
                _records, missing_fields, _note = await inspect_upstream(
                    slug, cfg.url, client
                )
                if missing_fields:
                    return False, f"fields absent upstream: {', '.join(missing_fields)}"
                return True, ""

            transient, retried_persistent = await resolve_with_retry(
                retryable, retry_one, attempts, _retry_delay()
            )
        persistent = {
            slug: why
            for slug, why in failures.items()
            if slug in missing_modules or slug in retried_persistent
        }

    for slug, why in transient.items():
        print(f"  TRANSIENT  {slug}: {why}")
        print("             passed on retry — reported, not counted as a failure")

    if persistent:
        print(f"\n{len(persistent)} FAILURE(S):")
        for slug, why in persistent.items():
            print(f"  - {slug}: {why}")
        return 1

    print(f"\nall {len(results)} modules reachable, parsed, and reading fields that exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
