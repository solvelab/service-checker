# 🔔 Notifications
![Notifications](https://img.shields.io/badge/Notifications-Telegram%20%7C%20Webhook-26A5E4)
![Flow](https://img.shields.io/badge/Lifecycle-Alert%20%7C%20Resolved%20%7C%20Monitoring--failure-2EA44F)

🔗 Nav: [🏠 Home](../../README.md) · [🎮 Steam](../modules/steam/README.md) · [🐳 Docker](../../DOCKER.md)

The `NotificationManager` (in `app/core/notifications.py`) receives module results and dispatches each enabled channel whenever a monitor returns `ALERT` or when a service returns to `OK` (resolution message).

## 🧭 Overview
- Each module is responsible for calling the `NotificationManager` handler; the core does not need to know destination details.
- For modules that return a list of services (Steam/OpenAI/etc.), the lifecycle is per service (independent alert, repeat, and resolution).
- Recovery notifications identify the exact component that transitioned back to OK, including name, id/slug, and the previous status. This keeps component-level recoveries distinct from provider-level "overall" recoveries.
- Each recovery notification is logged with `check_id` (e.g., `openai:api`) and the `from_status` → `to_status` transition for auditability.
- Available channels: Telegram (`app/notifications/telegram`), Webhook (`app/notifications/webhook`), Google Chat (`app/notifications/google_chat`) and Alertmanager (`app/notifications/alertmanager`).
- Channels are registered, not hardcoded — see *Adding a channel* below.
- Notification failures are logged at `ERROR` level but do not stop the main monitor —
  and the channel reports the failure back, so the event is retried instead of being
  buried by the repeat throttle.

## 🛑 Monitoring failure vs. service degradation
Two different things can go wrong, and they are reported separately:

| Event | Meaning | Telegram | Webhook `status` |
|---|---|---|---|
| Service alert | The provider says a service is degraded | 🚨 `ALERT` | `ALERT` |
| Service recovery | That service is operational again | ✅ `Resolved` | `RESOLVED` |
| Monitoring failure | **We cannot reach the provider at all** | 🛑 `Monitoring failure` | `MONITOR_ERROR` |
| Monitoring recovery | Checks are running again | 🔄 `Monitoring restored` | `MONITOR_RECOVERED` |

A monitor that cannot evaluate its upstream used to be completely silent — only a log line per cycle. It now reports itself after `NOTIFICATION_ERROR_THRESHOLD` consecutive failed checks (default `3`), and announces when it recovers.

Details of the lifecycle:
- Failures below the threshold are silent, so a single timeout does not page anyone.
- Any successful evaluation — `OK` **or** `ALERT` — resets the counter, because reaching the
  provider and finding it degraded still means monitoring works.
- A failure that was never announced recovers silently: there is nothing to retract.
- Sustained failures respect `NOTIFICATION_REPEAT_MINUTES`, so a day-long outage does not
  produce one card per cycle.
- A pending service alert survives a monitoring outage: `ALERT` → long failure → `OK` emits
  **both** the monitoring recovery and the service recovery, and the latter still reports the
  original alert text as `from_status`.
- **To silence monitoring-failure notifications**, set `NOTIFICATION_ERROR_THRESHOLD` to a
  value higher than any outage you expect to see. The minimum accepted value is `1`.

## 🔌 Adding a channel

`NotificationManager` keeps a registry and dispatches to every entry; it does not know
any channel by name. A channel is any object implementing the four methods of the
`Notifier` protocol in `app/core/types.py`:

| Method | Fired when |
|---|---|
| `send_alert` | a monitored service degraded |
| `send_recovery` | that service came back |
| `send_monitor_error` | the checker cannot reach the provider at all |
| `send_monitor_recovered` | the checker can reach it again |

All four take the same keyword arguments: `module_id`, `result`, `interval_seconds`,
`level_name`, `event_name`, `event_time`, `http_client`, `logger`.

**All four return `bool`: whether at least one target accepted the message.** This is not
bookkeeping — `NotificationManager` only advances the alert state when some channel
returns `True`. A channel that returns `False` gets the event retried on the next cycle;
one that wrongly returns `True` puts the state past an alert nobody received, and
`NOTIFICATION_REPEAT_MINUTES` then suppresses the repeat.

That is not hypothetical: every channel used to return `None`, so the gate never closed
and a webhook answering `500` counted as delivered. Return `False` for a transport
exception **and** for a response with status ≥ 400. A channel writing to several targets
returns `True` when any of them accepted — someone read it, and resending would duplicate
the message for them. `tests/test_notifier_contract.py` fails any channel that does not
declare and honour this.

To wire one up, build it from config in `NotificationManager.__init__` and register it:

```python
if config.mychannel.enabled:
    self.register("mychannel", MyChannelNotifier(config.mychannel))
```

`register()` verifies the four methods and raises on construction if any is missing —
a channel lacking `send_monitor_recovered` must not look healthy until the first
monitoring outage recovers in production.

**Channels are isolated.** An exception escaping one is logged as `notify_error` with
that channel's name, and the remaining channels still receive the event. Implementations
should still swallow their own transport failures; the isolation is a backstop, not a
license. Swallowing is not hiding: swallow the exception, log the reason, and return
`False`.

A return value that is not a `bool` is treated as **not delivered** and logged as a
contract violation. Counting it as delivered is precisely the defect this contract
replaced.

Two things to get right, both learned the hard way:
- Render from `MonitorResult.reason_items`, never by re-splitting the `reason` string —
  no separator is safe across providers.
- Keep the four events visually distinct. "The service is down" and "I cannot check"
  are different pages for whoever is on call.

To check a change end to end, without touching any provider or sending anything:

```bash
python scripts/simulate_notifications.py
```

It drives the real state machine through a full lifecycle with the real notifiers and
templates, intercepting only the outbound POST, and fails if any channel misses any event.

## 🔧 Variables
- `TELEGRAM_*`: enables the bot, provides the token, allows multiple chat_ids (`TELEGRAM_CHAT_IDS`), and optionally overrides the API URL (`TELEGRAM_API_URL`). Use negative IDs for groups.
- `NOTIFICATION_REPEAT_MINUTES`: minimum time (minutes) to repeat alerts for the same service while an incident persists (default `10`).
- `NOTIFICATION_ERROR_THRESHOLD`: consecutive failed checks before a module reports itself as broken (default `3`, minimum `1`). Also throttled by `NOTIFICATION_REPEAT_MINUTES`.
- `WEBHOOK_*`: enables delivery and sends a JSON POST to `WEBHOOK_URL`, with an optional token in `WEBHOOK_HEADER_NAME`.

## 📚 Recommended reading
- [Telegram](telegram/README.md): how to validate the token (`getMe`), find `chat_id` via `getUpdates`, and the card template.
- [Webhook](webhook/README.md): payload, headers, and usage examples.
- [Google Chat](google_chat/README.md): incoming-webhook setup, why the URL is a credential, the per-space quota, and threading.
- [Alertmanager](alertmanager/README.md): label and annotation mapping, and why `endsAt` is what keeps alerts from flapping.
