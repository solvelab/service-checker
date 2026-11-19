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

import logging
import re
from typing import Dict, List

import httpx

#: Incident statuses that mean "over" on Statuspage and incident.io pages.
_CLOSED_INCIDENT = frozenset({"resolved", "postmortem", "completed"})
#: Page incidents attached to one component; the card is not a feed.
INCIDENTS_PER_ITEM = 3
#: Pages that answered 404 on `incidents/unresolved.json` (incident.io): asked once per
#: process, so an outage costs one lookup per alerting cycle, not two.
_NO_UNRESOLVED: set = set()


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


async def attach_open_incidents(
    payload: object,
    http_client: httpx.AsyncClient,
    config,
    logger: logging.Logger,
) -> None:
    """Fetch the page's open incidents and attach them to the alerting items (#142).

    For modules that do not fetch incidents already (`openai`, `claude`).
    `incidents/unresolved.json` first; an incident.io page answers it with 404, so then
    `incidents.json`, filtered to open ones. Any failure leaves the items as they were:
    this decorates an alert, it never decides one.
    """
    if not isinstance(payload, list) or not payload or config is None:
        return
    base = config.url.rsplit("/api/", 1)[0]
    headers = {"User-Agent": config.user_agent}
    try:
        incidents = None
        paths = ("/api/v2/incidents/unresolved.json", "/api/v2/incidents.json")
        for path in paths[1:] if base in _NO_UNRESOLVED else paths:
            response = await http_client.get(
                f"{base}{path}", timeout=config.timeout_seconds, headers=headers
            )
            if response.status_code == 404 and path == paths[0]:
                _NO_UNRESOLVED.add(base)
                continue
            response.raise_for_status()
            incidents = response.json().get("incidents")
            break
        attach_incidents(payload, incidents)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "page incidents lookup failed; alert goes out without them",
            extra={"event": "incident_lookup_failed", "reason": f"{type(exc).__name__}: {exc}"},
        )
        return


def attach_incidents(payload: object, incidents: object) -> None:
    """Put open incident titles on each component item they concern (#142).

    A component name says *where* (`Responses`), the incident title says *what*
    ("Elevated error rates for gpt-6 Luna API requests"). An incident that lists
    components goes on those; one that lists none (incident.io never does) goes on
    every item. All of them are in `incidents`, as since #142; the ones that list no
    component are repeated in `page_incidents`, so the card can say it may be about
    another component (#146). Anything malformed is skipped, never raised.
    """
    if not isinstance(payload, list) or not isinstance(incidents, list):
        return
    open_incidents = []
    for inc in incidents:
        if not isinstance(inc, dict) or not inc.get("name"):
            continue
        if str(inc.get("status") or "").strip().lower() in _CLOSED_INCIDENT:
            continue
        listed = inc.get("components")
        components = {
            str(c.get("name")).lower()
            for c in (listed if isinstance(listed, list) else [])
            if isinstance(c, dict) and c.get("name")
        }
        open_incidents.append((str(inc["name"]), components))
    for item in payload:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).lower()
        titles = [title for title, components in open_incidents
                  if not components or name in components][:INCIDENTS_PER_ITEM]
        page_wide = [title for title, components in open_incidents if not components]
        if titles:
            item["incidents"] = titles
            shown_wide = [t for t in titles if t in page_wide]
            if shown_wide:
                item["page_incidents"] = shown_wide
