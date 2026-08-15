# Change: Add dedicated GitHub Status monitor to Service Checker

## Why
A relevant GitHub service instability happened recently, and the current Service Checker does not have a dedicated monitor that translates the official GitHub Status feed into the app's standard health model.

Adding a first-class `github` monitor reduces diagnosis time, improves incident visibility, and isolates failures in the GitHub Status API so they do not impact the monitoring loop of other providers.

## What Changes
- Add a new independent monitor named `github` under `scripts/service-checker/monitors/github` following the same monitor contract, return payload shape, logging/error conventions, and scheduler/polling behavior used by existing monitors.
- Consume GitHub Status public API endpoints to retrieve platform status, components, active incidents, and active maintenances.
- Normalize GitHub Status states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`) into the Service Checker severity model (`OK`/`WARNING`/`CRITICAL`/`UNKNOWN` or existing equivalent).
- Implement resilience controls: timeout, bounded retries, controlled fallback to `UNKNOWN` on API/network failures, and clear reason strings.
- Emit minimally useful degraded-state details: overall status, affected components (name + status), and active incident/maintenance summary (title + state + timestamp).
- Add monitor configuration (enable/disable, interval, timeout, retries) through the same configuration mechanism already used by the app, with safe defaults.
- Standardize observability tags for the new monitor, including monitor identifier (`github`), reason message, check duration, and consecutive failure counters where such telemetry already exists.

## Impact
- Affected specs: `github-monitor` (new capability)
- Affected code:
  - `scripts/service-checker/monitors/github` (new module)
  - shared monitor registry/loader and config plumbing (if needed)
  - shared logging/metrics adapter paths used by existing monitors
