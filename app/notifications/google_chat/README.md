# 💬 Google Chat Notifier
![Channel](https://img.shields.io/badge/Channel-Google%20Chat-1A73E8)
![Method](https://img.shields.io/badge/Method-POST%20cardsV2-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🔔 Notifications](../README.md) · [✈️ Telegram](../telegram/README.md) · [🔗 Webhook](../webhook/README.md) · [🔥 Alertmanager](../alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Posts a card to a Google Chat space through an incoming webhook, for all four lifecycle
events: service alert, service recovery, monitoring failure and monitoring recovery.

## 🔐 The webhook URL is a credential

```
https://chat.googleapis.com/v1/spaces/<SPACE_ID>/messages?key=<KEY>&token=<TOKEN>
```

`token` is unique to that webhook and grants the ability to post into the space. Treat the
whole URL as a secret: keep it out of version control, out of the image, and out of any
paste. In Kubernetes, move it to a `Secret` before enabling the channel.

This module never writes the URL to a log, including on error paths — logs identify the
destination by the space id from the path (`AAQAtjsc1Dk`), which carries no credential.
There is a test asserting no log line contains `key=` or `token=`.

## 🔧 Environment variables (`GOOGLE_CHAT_`)
- `ENABLED`: `true/false` (default `false`)
- `WEBHOOK_URL`: the incoming-webhook URL — required when enabled
- `MIN_INTERVAL_SECONDS`: minimum gap between two sends (default `1.1`)
- `THREAD_BY_CHECK`: group an alert and its recovery into one conversation (default `true`)

## 🎴 Why `cardsV2` and not plain `text`

A card's `textParagraph` renders **HTML tags** — `<b>`, `<i>`, `<code>`, `<br>`, `<a href>` —
so dynamic content is escaped with the same `html.escape` discipline the Telegram channel
already uses.

The plain `text` field would instead apply Chat's own markup, where `*`, `_`, backtick and
`<url|text>` all carry meaning. Supporting it safely would mean writing a new escaper, and a
new escaper is new surface for defects. The card also gives a real `header`, which is what
makes the four event types distinguishable at a glance.

Cards are built as Python dicts, not from Jinja2 templates: the payload is JSON, and
generating JSON from a text template is not how this project does it anywhere else.

## ⏱️ The one-request-per-second quota

A Chat space accepts **1 request per second**, shared across every webhook in that space.
Per-service modules emit one notification per component, so a provider with a dozen degraded
components produces a dozen sends in a single cycle.

The channel therefore paces itself: it records when it last sent and waits out the remainder
of `MIN_INTERVAL_SECONDS` before the next one.

Two consequences worth knowing:

- **Pacing delays the other channels for that event.** `NotificationManager` dispatches
  channels sequentially, so waiting here holds up whatever comes after. With a 60s check
  interval and rare bursts this is tolerable; if it stops being tolerable, the fix is a
  background queue, which needs a task lifecycle the application does not have today.
- **HTTP 429 is logged, not retried.** Pacing is the prevention; retrying inside an already
  throttled channel only stacks delay onto the next event. A 429 means the pacing was not
  enough — raise `MIN_INTERVAL_SECONDS`, or check whether another webhook shares the space.

## 🧵 Threading

With `THREAD_BY_CHECK=true`, the `threadKey` is derived from the same component key the alert
state uses (`rockstar:fivem`), so an alert and its recovery land in one conversation. The
`messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD` parameter is appended to the URL, so
a thread that does not exist yet is created.

The component half of that key comes from the provider payload, which is untrusted input, so
it is slugified before use — the same treatment `cardId` gets.

## ⚡ Setup
1. In the target space: **Apps & integrations → Webhooks → Add webhook**, and copy the URL.
2. Set `GOOGLE_CHAT_ENABLED=true` and `GOOGLE_CHAT_WEBHOOK_URL=<url>`.
3. Verify delivery without touching a provider or sending anything real:
   ```bash
   python scripts/simulate_notifications.py
   ```
