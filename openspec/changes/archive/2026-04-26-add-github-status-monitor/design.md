## Context
Service Checker currently monitors external providers but lacks a dedicated GitHub Status source. The new monitor must follow the same execution, error-handling, and reporting contract as existing monitors, while consuming GitHub Status data from `githubstatus.com`.

## Goals / Non-Goals
- Goals:
  - Add a monitor-scoped integration for GitHub Status that reports standardized severity and useful incident context.
  - Ensure GitHub monitor failures are isolated and do not block other monitors.
  - Keep behavior configurable using existing app-level monitor configuration mechanisms.
- Non-Goals:
  - Historical persistence beyond what the app already provides.
  - New UI paradigms or custom storage specific to GitHub.

## Endpoints
Primary endpoints to query from `https://www.githubstatus.com/api/v2`:
- `/status.json` for overall status (`status.indicator`, `status.description`)
- `/components.json` for component list and per-component status
- `/incidents.json` for incidents (filter to active/investigating/identified/monitoring as applicable)
- `/scheduled-maintenances.json` for active/in-progress maintenances

Design note: endpoint set may be optimized during implementation if one endpoint already includes needed fields with fewer calls, but the monitor must still return all required detail fields.

## Severity Mapping
Service Checker normalized levels (or exact project equivalent) should map consistently:
- `operational` -> `OK`
- `degraded_performance` -> `WARNING`
- `partial_outage` -> `CRITICAL`
- `major_outage` -> `CRITICAL`
- `under_maintenance` -> `WARNING`
- endpoint/parse/network timeout failure -> `UNKNOWN`

Final monitor severity should follow highest-impact signal among:
- overall platform indicator
- non-operational components
- active incidents / active maintenances

## Payload Handling Examples
Example status payload fields consumed:
- `status.indicator`
- `status.description`

Example component fields consumed:
- `components[].name`
- `components[].status`

Example incident/maintenance fields consumed:
- `incidents[].name`
- `incidents[].status`
- `incidents[].created_at` or `updated_at`
- `scheduled_maintenances[].name`
- `scheduled_maintenances[].status`
- `scheduled_maintenances[].scheduled_for` or `updated_at`

Returned monitor details for degraded/active-event states must include at least:
- overall status
- list of affected components (name + status)
- one active incident/maintenance summary (title + state + timestamp)

## Resilience & Failure Isolation
- Use per-request timeout and bounded retries (no infinite retry loops).
- On total API failure (DNS/network/timeout/non-parseable payload), emit controlled `UNKNOWN` with clear reason (e.g., `API timeout`, `DNS failure`, `invalid payload`).
- Keep failure local to `github` monitor execution; scheduler continues running other monitors in same cycle.
- Recover automatically on next successful poll without manual intervention.

## Configuration
Add GitHub monitor settings through existing config mechanism:
- `enabled` (default true)
- `interval` (safe default aligned with existing monitor cadence)
- `timeout` (safe default for external API checks)
- `retries` (small bounded default)

## Observability
Log and telemetry conventions for `github` monitor:
- include monitor id: `github`
- include reason string for non-OK/UNKNOWN (e.g., `major_outage detected`, `API timeout`)
- include check duration metric where existing framework supports it
- include consecutive failure counters where existing framework supports it

## Risks / Trade-offs
- GitHub Status API schema variations may occur; mitigation: defensive parsing and clear fallback to `UNKNOWN`.
- Multiple endpoint calls can increase latency; mitigation: bounded timeouts and optional endpoint consolidation if equivalent data is available.

## Validation Strategy
- Healthy live check: GitHub operational -> `OK` with matching overall indicator.
- Active incident/degradation check (live or fixture): escalates to `WARNING`/`CRITICAL` with required details.
- Failure injection (DNS/timeout): `UNKNOWN`, explicit log reason, automatic recovery after unblock.
- Isolation check: other monitors continue to update while GitHub monitor fails.
