## 1. Design
- [x] 1.1 Confirm the canonical monitor interface/contract and identify exact extension points to register a new independent monitor `github`.
- [x] 1.2 Finalize endpoint strategy (`status`, `components`, `incidents`, `scheduled-maintenances`) and request sequencing with timeout/retry budget.
- [x] 1.3 Confirm severity normalization matrix and precedence rules (overall vs components vs incidents/maintenances) against existing app status semantics.
- [x] 1.4 Define structured detail payload and logging keys to match current monitor output conventions.

## 2. Implementation
- [x] 2.1 Create `app/modules/github` module implementing the shared monitor contract.
- [x] 2.2 Implement GitHub Status API client calls with bounded timeout, retry, and defensive payload parsing.
- [x] 2.3 Implement state normalization into app severities and include degraded-state detail fields (overall, components, active incident/maintenance).
- [x] 2.4 Add controlled failure path to `ERROR` (app equivalent of `UNKNOWN`) for API/network errors without throwing process-level failures.
- [x] 2.5 Register `github` monitor in the loader/registry and ensure independent scheduling with existing monitors.
- [x] 2.6 Add monitor config options (`enabled`, `interval`, `timeout`, `retries`) with safe defaults via current config mechanism.
- [x] 2.7 Integrate observability hooks (monitor id, reason, duration, consecutive failures where supported).

## 3. Validation
- [x] 3.1 Validate healthy scenario against live `githubstatus.com` response (`OK` + matching operational indicator).
- [x] 3.2 Validate degraded/incident scenario (live when available or fixture/mocked payload) produces `ALERT` and required details.
- [x] 3.3 Validate resilience by simulating network/DNS/timeout failure and confirming controlled `ERROR` plus automatic recovery.
- [x] 3.4 Validate failure isolation: other monitors continue updating while `github` monitor is failing.
