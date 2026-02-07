# 🔔 Notifications
![Notifications](https://img.shields.io/badge/Notifications-Telegram%20%7C%20Webhook-26A5E4)
![Flow](https://img.shields.io/badge/Lifecycle-Alert%20%26%20Resolved-2EA44F)

🔗 Nav: [🏠 Home](../../README.md) · [🎮 Steam](../modules/steam/README.md) · [🐳 Docker](../../DOCKER.md)

The `NotificationManager` (in `app/core/notifications.py`) receives module results and dispatches each enabled channel whenever a monitor returns `ALERT` or when a service returns to `OK` (resolution message).

## 🧭 Overview
- Each module is responsible for calling the `NotificationManager` handler; the core does not need to know destination details.
- For modules that return a list of services (Steam/OpenAI/etc.), the lifecycle is per service (independent alert, repeat, and resolution).
- Recovery notifications identify the exact component that transitioned back to OK, including name, id/slug, and the previous status. This keeps component-level recoveries distinct from provider-level "overall" recoveries.
- Each recovery notification is logged with `check_id` (e.g., `openai:api`) and the `from_status` → `to_status` transition for auditability.
- Available channels: Telegram (`app/notifications/telegram`) and Webhook (`app/notifications/webhook`). New destinations can be added following the same contract.
- Notification failures are logged at `ERROR` level but do not stop the main monitor.

## 🔧 Variables
- `TELEGRAM_*`: enables the bot, provides the token, allows multiple chat_ids (`TELEGRAM_CHAT_IDS`), and optionally overrides the API URL (`TELEGRAM_API_URL`). Use negative IDs for groups.
- `NOTIFICATION_REPEAT_MINUTES`: minimum time (minutes) to repeat alerts for the same service while an incident persists (default `10`).
- `WEBHOOK_*`: enables delivery and sends a JSON POST to `WEBHOOK_URL`, with an optional token in `WEBHOOK_HEADER_NAME`.

## 📚 Recommended reading
- [Telegram](telegram/README.md): how to validate the token (`getMe`), find `chat_id` via `getUpdates`, and the card template.
- [Webhook](webhook/README.md): payload, headers, and usage examples.
