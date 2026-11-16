# 🔔 Telegram Notifier
![Channel](https://img.shields.io/badge/Channel-Telegram-2CA5E0)
![Format](https://img.shields.io/badge/Format-HTML-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🔔 Notifications](../README.md) · [🔗 Webhook](../webhook/README.md) · [💬 Google Chat](../google_chat/README.md) · [🔥 Alertmanager](../alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Keeps **one message per chat per day** (`TELEGRAM_MODE=daily_summary`, the default), edited as problems open and close. With `TELEGRAM_MODE=per_event` it sends one HTML card per event instead: when a module returns `ALERT`, when a service is resolved (`RESOLVED`), and for monitoring failures and their recovery.

## 🔧 Variables (`TELEGRAM_`)
- `TELEGRAM_ENABLED`: `true/false` to enable the channel (default `false`).
- `TELEGRAM_BOT_TOKEN`: bot token (required when enabled); the request uses `/bot${TOKEN}/`.
- `TELEGRAM_CHAT_ID`: chat or group (used if `TELEGRAM_CHAT_IDS` is empty).
- `TELEGRAM_CHAT_IDS`: comma-separated list to send to multiple chats/groups (use negative IDs for Telegram groups).
- `TELEGRAM_API_URL`: API endpoint (default `https://api.telegram.org`). Useful for proxies or custom environments.
- `TELEGRAM_TIMESTAMP_FORMAT`: format string used for the timestamp line (default `%Y-%m-%d %H:%M:%S %Z`).
- `TELEGRAM_TIMESTAMP_ZONE`: `UTC` (default) or `LOCAL`, determines whether the timestamp uses UTC or the host timezone (per-event cards).
- `TELEGRAM_MODE`: `daily_summary` (default) or `per_event`.
- `TELEGRAM_SUMMARY_TIMEZONE`: IANA zone where the summary day starts and in which it shows times (default `America/Sao_Paulo`).

## 📰 Daily summary (default)
One card per event buried the group: an open incident is re-sent every `NOTIFICATION_REPEAT_MINUTES`,
which in production was a card every ten minutes for one unchanged `claude` incident (~144 a day), and
a flapping `steam` sent 101 messages in 14 hours. People stopped reading the chat (issue #100).

The summary is one message per chat per day:

```
🔴 Service-Checker — 16/11/2026
1 problema(s) em aberto · 3 no dia

STEAM · 2 problema(s) hoje
🔴 Steam Community — minor · desde 14:02
✅ Steam Store · 09:10 → 09:41 (31 min)

CLAUDE · 1 problema(s) hoje
✅ claude · 00:34 → 02:10 (1h36)

🛑 Falhas de monitoramento (o checker não enxerga a fonte)
✅ steam · 10:05 → 10:20 (15 min)
```

- **A new problem rings.** The bot deletes the day's message and sends it again at the bottom of the
  chat, with a notification. Problems opening within 60 seconds of that resend only edit it, so one
  incident with several components rings once.
- **Everything else is a silent edit**: repeats, recoveries and monitoring restored. A repeat that
  changes nothing makes no API call.
- **Day rollover**: the first event after midnight (in `TELEGRAM_SUMMARY_TIMEZONE`) starts a new message
  and carries the problems still open; the previous day's message stays as the record.
- **Restart**: the day (entries and each chat's `message_id`) is saved in the state file under
  `telegram_summary`, so a restart keeps editing the same message. Without `NOTIFICATION_STATE_PATH`, a
  restart starts a new one.
- **Bot API failures never go silent**: `message is not modified` counts as delivered; a message that
  can no longer be edited is replaced by a new one; a failed delete does not stop the resend; a
  rate-limit, 5xx or network error reports "not delivered" so the manager retries next cycle, and
  the end of every check cycle rewrites any chat that missed an update (a refused recovery edit is
  never re-dispatched by the manager). A problem whose ring failed rings on the next write, whatever
  triggers it.
- **Size**: over 4096 characters, resolved problems collapse to the last 3 per service plus a count;
  open problems are always shown.
- In a group, the bot can delete its own messages for 48 hours, which always covers the day's message.
- Rollback without a new image: `TELEGRAM_MODE=per_event`.

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
3. Start the monitor. In the default mode the first problem of the day creates the day's message in every configured chat; with `TELEGRAM_MODE=per_event`, each ALERT sends a card to all of them.

## ℹ️ Notes
- Send failures (timeouts, blocked chat) are logged as `notify_error` and do not abort the process.
- For new channels, create subdirectories under `app/notifications/<channel>` and register them in `NotificationManager`.
## ✅ Delivery contract
Os quatro `send_*` devolvem `bool`: `True` quando **algum** `chat_id` aceitou, `False`
quando nenhum aceitou. Sucesso parcial conta como entrega — alguem leu, e reenviar no
ciclo seguinte duplicaria a mensagem para quem ja viu. Uma resposta com status >= 400 e
uma excecao de transporte contam igual: nao houve entrega naquele chat.

`NotificationManager` so avanca o estado do alerta com `True`. Enquanto este canal
devolvia `None`, um bot com token revogado contava como entrega e o alerta era engolido
pelo throttle de `NOTIFICATION_REPEAT_MINUTES`.
