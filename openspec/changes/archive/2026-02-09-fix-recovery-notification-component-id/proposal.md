# Change: Fix recovery recovery notifications identify component-specific transitions

## Why
Recovery alerts for providers with multiple components (e.g., OpenAI API/Chat/Images) currently announce a generic provider return to OK, making it impossible to know which component actually recovered.

## What Changes
- Include provider + component identifiers (name and slug) in recovery notifications, sourced from the same change event that detected the transition.
- Align recovery message formatting with incident (downtime) notifications so the component name stays consistent across the lifecycle.
- Log the unique check identifier and the from → to statuses when emitting a recovery notification for auditability.
- Keep provider-level “overall” status notifications separate from component recoveries to avoid implying full provider restoration.

## Impact
- Affected specs: notifications
- Affected code: monitoring state diff that produces status change events, notification formatters (Telegram/Webhook), notification logging
