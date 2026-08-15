# notifications Specification

## Purpose
TBD - created by archiving change fix-recovery-notification-component-id. Update Purpose after archive.
## Requirements
### Requirement: Recovery notifications identify the exact component transition
The system SHALL generate recovery notifications using the specific check that transitioned from a non-OK state to OK, including provider name, component name, component identifier/slug (when available), previous status, current status, and the transition timestamp extracted from the state-change event.

#### Scenario: Component recovers from non-OK to OK
- **WHEN** a provider component transitions from DOWN, DEGRADED, or UNKNOWN to OK based on a state-change event
- **THEN** the emitted notification includes provider name, component name, component slug/id (if present), previous status, current status, and the event's timestamp.

#### Scenario: Provider with multiple components
- **WHEN** a provider has multiple components and only one transitions to OK
- **THEN** the notification references that specific component (not just the provider) and uses the same identifier format that was used in the incident notification for that component.

### Requirement: Distinguish component recovery from provider overall status
The system SHALL keep component-level recovery notifications distinct from provider-level aggregates and SHALL only label a notification as provider-level “overall” when the aggregate status itself transitions.

#### Scenario: Partial provider recovery
- **WHEN** one component returns to OK while another component of the same provider remains non-OK
- **THEN** the recovery message states only that the specific component recovered and does not imply full provider restoration; any aggregate notification, if sent, is explicitly labeled “Overall”.

### Requirement: Log component transition metadata for recoveries
The system SHALL log (debug or info) the unique check identifier and the from_status → to_status values whenever a recovery notification is emitted.

#### Scenario: Logged recovery transition
- **WHEN** a recovery notification is generated for a component
- **THEN** the logs contain the component’s check_id (provider_id + component_id/slug) and the previous and current statuses to support auditing of notification content.

