# 🔔 Telegram Notifier
![Channel](https://img.shields.io/badge/Channel-Telegram-2CA5E0)
![Format](https://img.shields.io/badge/Format-HTML-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🔔 Notifications](../README.md) · [🔗 Webhook](../webhook/README.md) · [💬 Google Chat](../google_chat/README.md) · [🔥 Alertmanager](../alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Sends HTML cards to configured chats or groups when a module returns `ALERT` and when a service is resolved (`RESOLVED`). The default text includes module, status, reason, send time, payload summary, and measured check duration.

## 🔧 Variables (`TELEGRAM_`)
- `TELEGRAM_ENABLED`: `true/false` to enable the channel (default `false`).
- `TELEGRAM_BOT_TOKEN`: bot token (required when enabled); the request uses `/bot${TOKEN}/`.
- `TELEGRAM_CHAT_ID`: chat or group (used if `TELEGRAM_CHAT_IDS` is empty).
- `TELEGRAM_CHAT_IDS`: comma-separated list to send to multiple chats/groups (use negative IDs for Telegram groups).
- `TELEGRAM_API_URL`: API endpoint (default `https://api.telegram.org`). Useful for proxies or custom environments.
- `TELEGRAM_TIMESTAMP_FORMAT`: format string used for the timestamp line (default `%Y-%m-%d %H:%M:%S %Z`).
- `TELEGRAM_TIMESTAMP_ZONE`: `UTC` (default) or `LOCAL`, determines whether the timestamp uses UTC or the host timezone.

## ✅ Validating the bot and recipients
1. Check the token:  
   `curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"` should return `{"ok":true,...}` with the `username`.
2. Find the chat_id:
   - Send a message to the bot (or add it to a group and mention `@<bot_username>`).
   - Run `curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"` and look for `chat.id` in the JSON.
   - Alternatively, use bots like [@RawDataBot](https://t.me/RawDataBot) to expose the ID.
3. For groups, use the negative `chat.id` returned by `getUpdates` (e.g., `-4058878374`). This works for both `TELEGRAM_CHAT_ID` and `TELEGRAM_CHAT_IDS`.

## 🧩 Card template

### Alert
```
🚨 {MODULE_ID}
Level: {LEVEL}
Event: Service-Checker
Status: ALERT
Reason:
• {REASON_ITEMS}
Message: {MESSAGE}
Timestamp: {TIMESTAMP}
Duration: {DURATION_MS}ms
Interval: {INTERVAL}s
```

### Resolved
```
✅ Service-Checker — Resolved
Module: {MODULE_ID}

Level: INFO
Event: Service-Checker
Status: OK
Recovered component:
• {COMPONENT_NAME} ({COMPONENT_ID}) — {FROM_STATUS} → {TO_STATUS}
Message: service restored
Timestamp: {TIMESTAMP}
Duration: {DURATION_MS}ms
Interval: {INTERVAL}s
```

Para provedores com multiplos componentes (OpenAI, Steam, etc.), a notificacao de recuperacao identifica exatamente qual componente foi restaurado, incluindo o nome, id/slug e a transicao de status. Quando nenhum componente esta presente (recuperacao no nivel do modulo), a secao "Recovered component" e omitida.

`parse_mode=HTML` ensures the card displays with emphasis and clean separators.

Os templates HTML ficam em `app/notifications/telegram/templates/` — cinco, carregados no import
do notifier:
- `telegram_alert.j2` — alerta padrao
- `telegram_steam.j2` — alerta especifico do Steam com servicos impactados
- `telegram_resolved.j2` — mensagem de recuperacao com identificacao do componente
- `telegram_monitor_error.j2` — o checker nao consegue avaliar o provedor
- `telegram_monitor_recovered.j2` — o checker voltou a conseguir

Os dois ultimos existem para manter "o servico caiu" e "nao consegui checar" visualmente distintos:
sao paginas diferentes para quem esta de plantao.

## 🚀 How to use
1. Create the bot with [BotFather](https://t.me/BotFather) and grab the token.
2. Update `.env` with `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, and the chat/group via `TELEGRAM_CHAT_ID` or `TELEGRAM_CHAT_IDS`.
3. Start the monitor. Each ALERT will send the card to all configured chat_ids.

## ℹ️ Notes
- Send failures (timeouts, blocked chat) are logged as `notify_error` and do not abort the process.
- For new channels, create subdirectories under `app/notifications/<channel>` and register them in `NotificationManager`.
