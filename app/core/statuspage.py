"""Shared parsing for providers that publish a Statuspage v2 summary.

Five modules read the same document — `bitbucket`, `github`, `openai`, `claude` and
`cloudflare` — and until now each carried its own byte-identical copy of these two
functions. The duplication was not free: a payload whose `components` was not a list of
objects raised `AttributeError`, and fixing it meant fixing it four times. It was found
in one module and had to be reported as a defect in the other four.

Worse than the exception itself is where it escaped from. `_extract_components` runs
outside the try/except that wraps the HTTP request, so the exception left `check()`
entirely. The scheduler does catch it — but by then the module never returned a
`MonitorStatus.ERROR`, so the notification manager never saw a failed evaluation, the
error streak never counted, and the dead-monitor notification never fired. The breakage
lived in the log and nowhere else, which is precisely the failure mode that
notification was built to eliminate.

So the contract here is: **never raise for a payload shape**. Anything unusable comes
back as an empty list, and the caller turns that into `MonitorStatus.ERROR` with a
readable reason.
"""
from __future__ import annotations

import re
from typing import Dict, List


def extract_components(data: Dict) -> List[Dict]:
    """Normalise a Statuspage `components` array into `id`/`name`/`status`/`slug`.

    Returns `[]` for anything unusable — a missing key, a `components` that is not a
    list, or a list holding values that are not objects. Callers already treat an empty
    result as `MonitorStatus.ERROR`, so an empty return is the safe way to say "this
    payload is not what we parse".
    """
    if not isinstance(data, dict):
        return []
    components = data.get("components") or []
    if not isinstance(components, list):
        return []

    cleaned: List[Dict] = []
    for comp in components:
        # A dict iterates its own keys, and a list can hold anything. Both used to
        # reach `comp.get(...)` and raise.
        if not isinstance(comp, dict):
            continue
        name = comp.get("name") or "unknown"
        if not isinstance(name, str):
            name = str(name)
        comp_id = comp.get("id") or slugify(name)
        status = comp.get("status") or "unknown"
        cleaned.append(
            {
                "id": str(comp_id),
                "name": name,
                "status": status if isinstance(status, str) else str(status),
                "slug": slugify(name),
            }
        )
    return cleaned


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
