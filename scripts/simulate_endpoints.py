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

Exit code is non-zero when a module fails to load, raises, returns ERROR, or reads
fields absent from every upstream record.

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
    # steam and rockstar parse HTML behind TLS impersonation; the module's own parse
    # count is the only meaningful signal, so they carry no field contract.
}


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


async def inspect_upstream(slug: str, url: str, client) -> tuple[int, list[str], str]:
    """Return (record count, fields absent from every record, note)."""
    contract = CONTRACTS.get(slug)
    if contract is None:
        return -1, [], "no contract (HTML)"
    try:
        response = await client.get(url, timeout=20.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return -1, [], f"fetch failed: {type(exc).__name__}"

    kind = contract["kind"]
    if kind == "rss":
        records = [
            dict.fromkeys(re.findall(r"<(\w+)[ >]", chunk))
            for chunk in re.findall(r"<item>(.*?)</item>", response.text, re.S)
        ]
    else:
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return -1, [], "body is not JSON"
        if kind == "json-collection":
            data = data.get(contract["collection"], []) if isinstance(data, dict) else []
        records = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    if not records:
        return 0, [], "upstream reports no records"

    missing = [
        field
        for field in contract["record_fields"]
        if not any(field in record for record in records)
    ]
    return len(records), missing, ""


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

    failures: list[tuple[str, str]] = [(s, "module failed to load") for s in missing_modules]

    async with create_http_client(
        timeout_seconds=config.defaults.timeout_seconds,
        user_agent=config.defaults.user_agent,
    ) as client:

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
            failures.append((slug, f"uncaught exception: {raised}"))
            continue

        parsed = _count_parsed(result.payload)
        raw = "-" if records < 0 else str(records)
        verdict = note or "ok"
        if missing:
            verdict = f"BLIND — fields absent upstream: {', '.join(missing)}"
            failures.append((slug, verdict))
        print(
            f"{slug:<11} {result.status.value:<7} {elapsed:>7.0f} {raw:>5} {parsed:>7}  {verdict}"
        )
        if result.reason:
            print(f"{'':<11} {'':<7} {'':>7} {'':>5} {'':>7}  reason: {result.reason[:80]}")
        if result.status.value == "ERROR":
            failures.append((slug, result.reason or result.message))

    print("-" * 96)
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for slug, why in failures:
            print(f"  - {slug}: {why}")
        return 1

    print(f"\nall {len(results)} modules reachable, parsed, and reading fields that exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
