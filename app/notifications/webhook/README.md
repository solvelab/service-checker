# 🔔 Webhook Notifier
![Channel](https://img.shields.io/badge/Channel-Webhook-6E56CF)
![Method](https://img.shields.io/badge/Method-POST-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🔔 Notifications](../README.md) · [✈️ Telegram](../telegram/README.md) · [💬 Google Chat](../google_chat/README.md) · [🔥 Alertmanager](../alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Sends a POST to `WEBHOOK_URL` whenever a module enters `ALERT`, when a service returns to `OK` (`RESOLVED` event), when the monitor itself can no longer reach the provider (`MONITOR_ERROR`) and when it recovers (`MONITOR_RECOVERED`). You can attach a token in the header (`WEBHOOK_HEADER_NAME`) for authentication.

> ⚠️ **Contract note for consumers.** The `status` field is an open set. It currently takes
> `ALERT`, `RESOLVED`, `MONITOR_ERROR` and `MONITOR_RECOVERED`; more values may be added.
> A consumer that matches on the values it knows keeps working across upgrades. A consumer
> that raises on an unrecognised value will break — treat unknown values as informational
> and ignore them.

`MONITOR_ERROR` and `MONITOR_RECOVERED` are about the **monitoring**, not the monitored
service: they mean "the checker cannot reach this provider" and "the checker can reach it
again". The provider may have been perfectly healthy the whole time.

## 🔧 Variables (`WEBHOOK_`)
- `WEBHOOK_ENABLED`: `true/false` to enable the channel (default `false`).
- `WEBHOOK_URL`: receiver endpoint (required when enabled).
- `WEBHOOK_TOKEN`: optional token sent in the `WEBHOOK_HEADER_NAME` header.
- `WEBHOOK_HEADER_NAME`: header name (default `Authorization`).

## 🚀 Payload sent

### ALERT
```json
{
  "timestamp": "<iso8601>",
  "level": "<INFO|WARNING|ERROR>",
  "event": "<monitor_check|service_alert>",
  "module": "<module_id>",
  "status": "ALERT",
  "message": "<result.message>",
  "reason": "<result.reason>",
  "payload": "<result.payload>",
  "interval_seconds": "<int>"
}
```

### RESOLVED
```json
{
  "timestamp": "<iso8601>",
  "level": "INFO",
  "event": "<monitor_resolved|service_resolved>",
  "module": "<module_id>",
  "check_id": "<module_id>:<component_id>",
  "status": "RESOLVED",
  "message": "<result.message>",
  "reason": "<result.reason>",
  "payload": "<result.payload>",
  "interval_seconds": "<int>"
}
```

### MONITOR_ERROR
Emitted after `NOTIFICATION_ERROR_THRESHOLD` consecutive failed checks (default `3`), then repeated
at most every `NOTIFICATION_REPEAT_MINUTES`.

```json
{
  "timestamp": "<iso8601>",
  "level": "ERROR",
  "event": "monitor_failure",
  "module": "<module_id>",
  "check_id": "<module_id>",
  "status": "MONITOR_ERROR",
  "message": "monitoring failure",
  "reason": "<n> consecutive failed checks; last error: <detail>",
  "payload": null,
  "interval_seconds": "<int>"
}
```

### MONITOR_RECOVERED
Emitted when the module evaluates successfully again — but only if the corresponding
`MONITOR_ERROR` was actually sent.

```json
{
  "timestamp": "<iso8601>",
  "level": "INFO",
  "event": "monitor_failure_resolved",
  "module": "<module_id>",
  "check_id": "<module_id>",
  "status": "MONITOR_RECOVERED",
  "message": "monitoring restored",
  "reason": "upstream reachable again after <n> failed checks",
  "payload": null,
  "interval_seconds": "<int>"
}
```

`check_id` identifica de forma unica o componente recuperado (ex: `openai:api`, `steam:csgo`). Para recuperacoes no nivel do modulo (sem componentes), `check_id` e igual ao `module`.

## ⚙️ Usage example
1. Enable the channel: `WEBHOOK_ENABLED=true`.
2. Point `WEBHOOK_URL` to your endpoint and, if needed, set:
   - `WEBHOOK_TOKEN=Bearer abc123`
   - `WEBHOOK_HEADER_NAME=Authorization` (or another header expected by the receiver).
3. The monitor sends the JSON above on every ALERT and logs failures without stopping the process.
## ✅ Delivery contract
Os quatro `send_*` devolvem `bool`. `False` para excecao de transporte **e** para resposta
com status >= 400 — este canal nao olhava o status, entao um endpoint respondendo `500` a
cada requisicao era indistinguivel de um que funcionava. `NotificationManager` so avanca o
estado do alerta com `True`; com `False`, o ciclo seguinte reenvia em vez de o throttle
suprimir um alerta que ninguem recebeu.
