## ADDED Requirements
### Requirement: Independent GitHub status monitor
The Service Checker SHALL provide an independent monitor named `github` that follows the same monitor interface, return format, error conventions, and configurable polling cadence used by other monitors.

#### Scenario: Monitor is loaded and scheduled independently
- **WHEN** Service Checker starts with the GitHub monitor enabled
- **THEN** the `github` monitor is registered as an independent monitor with a consistent label/name
- **AND** it executes periodic checks according to configured interval
- **AND** failure in `github` execution does not stop scheduling for other monitors.

### Requirement: GitHub Status API ingestion
The `github` monitor SHALL query GitHub Status public API data to obtain overall platform status, component statuses, and relevant active incidents/maintenances.

#### Scenario: Monitor retrieves required status domains
- **WHEN** a poll cycle is executed successfully
- **THEN** the monitor reads overall platform indicator
- **AND** collects component status list
- **AND** collects incident and scheduled maintenance entries relevant to in-progress/active conditions.

### Requirement: Severity normalization to Service Checker model
The `github` monitor SHALL map GitHub Status indicators to Service Checker severity levels using a deterministic mapping.

#### Scenario: Status mapping is applied consistently
- **WHEN** GitHub Status returns `operational`, `degraded_performance`, `partial_outage`, `major_outage`, or `under_maintenance`
- **THEN** the monitor maps respectively to `OK`, `WARNING`, `CRITICAL`, `CRITICAL`, and `WARNING` (or exact existing project equivalents)
- **AND** the final severity for a poll reflects the highest-impact condition among overall status, components, and active incidents/maintenances.

### Requirement: Degradation details in monitor output and logs
When GitHub platform health is non-operational, the monitor SHALL expose minimally useful details compatible with existing monitor output conventions.

#### Scenario: Degraded state includes actionable context
- **WHEN** the computed severity is `WARNING` or `CRITICAL`
- **THEN** output/log details include overall platform status
- **AND** include at least one affected component with name and status when present
- **AND** include at least one active incident or maintenance summary with title, state, and timestamp.

### Requirement: Controlled failure behavior for GitHub Status API instability
The `github` monitor SHALL degrade to a controlled unknown/error state when GitHub Status API calls fail, without crashing the Service Checker process.

#### Scenario: API failure produces isolated UNKNOWN state
- **WHEN** requests to `githubstatus.com` fail due to timeout, DNS, network, or invalid payload
- **THEN** the monitor returns `UNKNOWN` (or existing equivalent controlled error state)
- **AND** logs the reason with monitor identifier `github`
- **AND** other monitors continue normal update cycles.

#### Scenario: Automatic recovery after connectivity returns
- **WHEN** API connectivity is restored after a prior `UNKNOWN` failure
- **THEN** subsequent scheduled polls resume normal severity computation and update monitor state automatically.

### Requirement: Configurable GitHub monitor parameters
The Service Checker SHALL allow configuring GitHub monitor enablement and execution parameters through the existing configuration mechanism.

#### Scenario: Monitor configuration is applied
- **WHEN** configuration values for `github.enabled`, `github.interval`, `github.timeout`, and `github.retries` are provided
- **THEN** the monitor respects those values
- **AND** safe defaults are applied when values are omitted.

### Requirement: Observability alignment for GitHub monitor
The `github` monitor SHALL emit logs and telemetry fields aligned with existing monitor observability patterns.

#### Scenario: Logs and metrics include monitor context
- **WHEN** a GitHub check completes in any state
- **THEN** logs include monitor identifier `github` and status reason
- **AND** check duration is captured where monitor telemetry exists
- **AND** consecutive failure counters are updated where this telemetry exists.
