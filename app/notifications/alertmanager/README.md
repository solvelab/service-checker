# 🔥 Alertmanager Notifier
![Channel](https://img.shields.io/badge/Channel-Alertmanager-E6522C)
![Method](https://img.shields.io/badge/Method-POST%20%2Fapi%2Fv2%2Falerts-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🔔 Notifications](../README.md) · [✈️ Telegram](../telegram/README.md) · [🔗 Webhook](../webhook/README.md) · [💬 Google Chat](../google_chat/README.md) · [🐳 Docker](../../../DOCKER.md)

Pushes incidents into an existing Alertmanager, so upstream provider outages land in the
same place as internal alerts and reuse the silences, grouping, inhibition and on-call
routes you already have.

## 🔧 Environment variables (`ALERTMANAGER_`)
- `ENABLED`: `true/false` (default `false`)
- `URL`: **base URL only**, e.g. `http://alertmanager:9093` — the channel appends `/api/v2/alerts`
- `TOKEN` / `HEADER_NAME`: optional auth header (default header `Authorization`)
- `EXTRA_LABELS`: static labels for routing, as `env=prod,cluster=main`
- `RESOLVE_AFTER_SECONDS`: `0` (auto) — read the next section before changing it

## ⏳ Why `endsAt` is the whole design

Alertmanager expects clients to **retransmit firing alerts continuously**. With `endsAt`
omitted it sets `now + resolve_timeout` (5 minutes by default) and resolves the alert on
its own once that elapses.

The Service Checker does the opposite. `NOTIFICATION_REPEAT_MINUTES` exists precisely to
*not* resend, and it suppresses the repeat **in the state machine**, before any channel is
called — a channel cannot opt out of a throttle it never sees.

Left alone, the two produce flapping: the alert self-resolves at 5 minutes, is recreated at
10, and alternates forever, paging on every flip.

So every firing alert carries an explicit `endsAt`, placed far enough ahead that the alert
cannot expire between two sends. The repeat window then *is* the heartbeat Alertmanager
asks for, with no change to the state machine. A recovery sends `endsAt` in the past, so the
alert resolves immediately instead of waiting the margin out.

**The margin must exceed the real gap between two sends.** The resend lands on the first
check cycle at or after the window elapses, so the real gap is `repeat + up to one interval`.
The automatic value doubles both:

```
endsAt = now + max(2 × repeat_seconds + 2 × interval_seconds, 300s)
```

With the defaults (repeat 10 min, interval 60 s) that is 22 minutes.

**The trade-off, stated plainly.** If the Service Checker dies, its alerts stay firing in
Alertmanager until the margin elapses, instead of resolving by Alertmanager's own timeout in
5 minutes. That is deliberate — a dead checker should not silently clear alerts — but it does
mean Alertmanager can show a stale alert for up to the margin. Shrinking
`RESOLVE_AFTER_SECONDS` buys a faster stale-alert cleanup at the price of flapping; anything
below `repeat + interval` will flap.

## 🏷️ Labels and annotations

`alertname` is fixed per event type, following the Prometheus convention that the alert name
is the *rule* and the labels identify the instance. A varying `alertname` would create a new
alert every cycle instead of deduplicating, and would break grouping and silencing.

| Label | Value |
|---|---|
| `alertname` | `ServiceCheckerIncident` or `ServiceCheckerMonitoringFailure` |
| `source` | `service-checker` |
| `module` | the provider slug, e.g. `github` |
| `check_id` | `module` or `module:component`, built from the raw payload id |
| `component` | present only when the payload names one |
| `severity` | `warning` for a service incident, `critical` for a monitoring failure |

Anything from `EXTRA_LABELS` is merged in, except the reserved names: static configuration must
not be able to redefine what identifies an alert. The reserved set is `RESERVED_LABELS` in
`notifier.py`, and it holds **seven** names — the six in the table above plus `event`, which is an
annotation here but is reserved as a label too. `ALERTMANAGER_EXTRA_LABELS=event=deploy` is dropped.

`check_id` here is **not** byte-identical to the alert-state key. `_service_key`
(`app/core/notifications.py`) lowercases; this channel concatenates the identifier as the payload
published it. For a component whose id carries an uppercase letter — a GCP incident id, an AWS
service name — the label and the state key differ in case, and so does the webhook's `check_id`,
which lowercases. Correlate case-insensitively.

| Annotation | Value |
|---|---|
| `summary` | the incident reason |
| `description` | the monitor message |
| `incidents` | one line per incident, when the monitor separated them |
| `event` | the internal event name |

**Free text never goes in a label.** A distinct reason per incident would create a new series
for every wording change — the classic cardinality blowout.

## 🧪 Smoke test against a real Alertmanager

The suite asserts the request body against the documented contract, but the project has no
testcontainers setup, so an end-to-end check is manual:

```bash
docker run --rm -p 9093:9093 prom/alertmanager

ALERTMANAGER_ENABLED=true ALERTMANAGER_URL=http://localhost:9093 \
  python scripts/simulate_notifications.py

curl -s localhost:9093/api/v2/alerts | jq '.[] | {labels, endsAt}'
```

Expect the alert to appear firing, then disappear once the recovery lands.

## 🚧 Not implemented
- `generatorURL` — the natural value is the provider's status page, and the notifier contract
  passes `interval_seconds` rather than the module config. Adding it would change the
  signature of all four methods across every channel.
- mTLS and OAuth — only a static header today.
