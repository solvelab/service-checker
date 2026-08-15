## 1. Implementation
- [x] 1.1 Locate the status change event/payload used for DOWN/DEGRADED/UNKNOWN → OK transitions and confirm it carries provider + component identifiers and timestamps.
- [x] 1.2 Standardize a unique check identifier (e.g., provider_id + component_id/slug) in that event for downstream notification formatting.
- [x] 1.3 Update notification builders (Telegram/Webhook or shared formatter) to include provider, component (name + slug/id), previous status, current status, and transition timestamp, mirroring the incident message pattern.
- [x] 1.4 Ensure component recovery notifications stay component-scoped when provider aggregates remain non-OK; only send/label an "overall" recovery when that aggregate actually changes.
- [x] 1.5 Add debug/info logging that records check_id and from_status → to_status for each recovery notification emitted.
- [x] 1.6 Add/extend tests or fixtures covering single-component recovery, staggered multi-component recoveries, and partial-provider degradation to validate message content and logging.
