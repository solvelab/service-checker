<p align="center">
  <img src="logo.png" alt="Service Monitor Logo" width="200">
</p>

# ⚙️ Service Monitor
![Python](https://img.shields.io/badge/Python-3.11-blue)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED)
![Notifications](https://img.shields.io/badge/Notifications-Telegram%20%7C%20Webhook%20%7C%20Google%20Chat%20%7C%20Alertmanager-26A5E4)
![Semantic Release](https://github.com/solvelab/service-checker/actions/workflows/release.yml/badge.svg)
![Publish Image](https://github.com/solvelab/service-checker/actions/workflows/publish.yml/badge.svg)
[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/donate/?business=ZUADM4SZT5DC8&no_recurring=0&item_name=Projetos+desenvolvidos+com+cuidado+e+dedica%C3%A7%C3%A3o.+O+apoio+incentiva+a+continuidade+e+a+evolu%C3%A7%C3%A3o+constante.&currency_code=BRL)

🔗 Nav: [🎮 Steam](app/modules/steam/README.md) · [🤖 OpenAI](app/modules/openai/README.md) · [🟣 Claude](app/modules/claude/README.md) · [🎮 Rockstar](app/modules/rockstar/README.md) · [☁️ OCI](app/modules/oci/README.md) · [🌐 GCP](app/modules/gcp/README.md) · [☁️ AWS](app/modules/aws/README.md) · [🐙 GitHub](app/modules/github/README.md) · [🪣 Bitbucket](app/modules/bitbucket/README.md) · [🔔 Notifications](app/notifications/README.md) · [🐳 Docker](DOCKER.md)

A modular Python monitor that continuously checks third-party status pages (Steam, OpenAI, Claude, Rockstar, OCI, GCP, AWS, GitHub, and Bitbucket) and sends configurable alerts when any module detects an incident.

## 📸 Preview

<table align="center">
  <tr>
    <td align="center">
      <img src="docs/screenshots/telegram-alert-and-recovery.png" alt="Steam alert and recovery" width="360"><br>
      <sub>Steam — alert + recovery</sub>
    </td>
    <td align="center">
      <img src="docs/screenshots/telegram-github-alert-and-recovery.png" alt="GitHub alert and recovery" width="360"><br>
      <sub>GitHub — alert + recovery</sub>
    </td>
  </tr>
</table>

## ✅ Highlights
- Modular, plug-in style monitors for popular status providers — adding one is a new file, not a
  change to the core.
- Four notification channels: Telegram, generic Webhook, Google Chat and Alertmanager. Channels
  register themselves and are isolated: one failing never silences the others.
- Two lifecycles, kept apart on purpose. **A degraded service** and **a checker that cannot reach
  the provider** are different pages for whoever is on call.
- Per-component alert lifecycle with repeat throttling, keyed so two simultaneous incidents never
  suppress one another.
- Behaviour written down as specs in [`openspec/specs/`](openspec/specs), not only as code.
- Docker-first deployment with sensible defaults.

## 🎯 Why this exists
- Reduce manual status page checks across multiple providers.
- Standardize alerting for incidents across heterogeneous sources.
- Keep the footprint small and operable with simple env config.

## 🧭 Use cases
- Ops teams wanting a single alert stream for upstream incidents.
- SREs watching provider health in specific regions or services.
- Personal or small-team monitoring without heavy tooling.

## 🧱 Project structure
- `app/`: core engine, module loaders, modules, and notifiers ([notifications](app/notifications/README.md)).
- `docker-compose.yml`, `Dockerfile`, and `.env(.example)`: local and container runtime ([Docker guide](DOCKER.md)).

## 📦 Modules
Each module pulls a provider-specific status source and applies rules configured via environment variables.

- 🎮 **Steam**: https://steamstat.us/ (HTML parsing with status/keyword/regex). [📖](app/modules/steam/README.md)
- 🤖 **OpenAI**: https://status.openai.com (`/api/v2/summary.json`). [📖](app/modules/openai/README.md)
- 🟣 **Claude**: https://status.claude.com (`/api/v2/summary.json`). [📖](app/modules/claude/README.md)
- 🎮 **Rockstar**: https://support.rockstargames.com/servicestatus (HTML parsing — covers FiveM/RedM after Cfx.re acquisition). [📖](app/modules/rockstar/README.md)
- ☁️ **OCI**: https://ocistatus.oraclecloud.com (RSS `incident-summary.rss`). [📖](app/modules/oci/README.md)
- 🌐 **GCP**: https://status.cloud.google.com (`incidents.json`). [📖](app/modules/gcp/README.md)
- ☁️ **AWS**: https://health.aws.amazon.com/public/currentevents (JSON events). [📖](app/modules/aws/README.md)
- 🐙 **GitHub**: https://www.githubstatus.com (`/api/v2/summary.json`). [📖](app/modules/github/README.md)
- 🪣 **Bitbucket**: https://bitbucket.status.atlassian.com (`/api/v2/summary.json`). [📖](app/modules/bitbucket/README.md)

See each module README for rules, filters, and examples.

## 🔔 Notifications
`NotificationManager` keeps the alert state and dispatches to every registered channel. It reports
four events, and the distinction between the first two and the last two matters:

| Event | Meaning |
|---|---|
| Service alert | the provider says a service is degraded |
| Service recovery | that service is operational again |
| **Monitoring failure** | **the checker cannot reach the provider at all** |
| Monitoring recovery | checks are running again |

A monitor that cannot evaluate its upstream is not a healthy provider, and never reports as one. It
speaks up after `NOTIFICATION_ERROR_THRESHOLD` consecutive failed checks (default `3`).

| Channel | Notes |
|---|---|
| [Telegram](app/notifications/telegram/README.md) | HTML cards for chats or groups |
| [Webhook](app/notifications/webhook/README.md) | JSON POST; `status` is an **open set** — `ALERT`, `RESOLVED`, `MONITOR_ERROR`, `MONITOR_RECOVERED` |
| [Google Chat](app/notifications/google_chat/README.md) | `cardsV2` messages; the webhook URL is a credential |
| [Alertmanager](app/notifications/alertmanager/README.md) | `POST /api/v2/alerts`, so incidents reuse your existing silences and routes |

All four are off by default. A channel that fails is logged and skipped — it cannot stop the
others from receiving the same event.

## 🚀 Quick start
1. Copy `.env.example` to `.env` and customize filters/tokens.
2. Run `docker compose up --build` from the repository root.
3. Monitor logs with `docker compose logs --tail 20`.

## 🗺️ Flow overview
```
Providers -> Modules -> Monitor Core -> NotificationManager -> Channels
              (9)                        state + throttle       (4)
```

One `asyncio` task per module, each on its own interval. A module that fails to load is logged and
skipped; one that raises is caught and logged. Neither takes the others down.

## 🧰 Configuration essentials
Everything is an environment variable; there is no config file.

- `SERVICE_MONITOR_MODULES`: comma-separated list of module slugs to load.
- `NOTIFICATION_REPEAT_MINUTES`: minimum interval before repeating an alert for the same component.
- `NOTIFICATION_ERROR_THRESHOLD`: consecutive failed checks before a module reports itself as broken.
- `TELEGRAM_ENABLED` / `WEBHOOK_ENABLED` / `GOOGLE_CHAT_ENABLED` / `ALERTMANAGER_ENABLED`: enable
  channels. All default to `false`.

Each module also supports its own `*_URL`, `*_INTERVAL_SECONDS`, `*_TIMEOUT_SECONDS`,
`*_SERVICE_FILTER` and `*_ENABLED` keys, prefixed with the uppercased slug. Not every module reads
every key — `steam` and `rockstar` ignore `*_RULE_KIND`, `*_RULE_VALUE` and `*_USER_AGENT`, because
they classify from the page text and fetch through TLS impersonation, which sets its own headers.
Each module README says what it actually reads.

Full reference in [DOCKER.md](DOCKER.md).

## 🔍 Verifying a change
The test suite runs against frozen fixtures, so it proves the parsers handle the payload of the day
they were captured. Three scripts cover what it cannot:

```bash
python scripts/simulate_notifications.py          # offline, deterministic
python scripts/simulate_endpoints.py .env.example # queries the nine real providers
python scripts/simulate_alerts.py                 # degrades each provider and checks it fires
```

- **`simulate_notifications.py`** drives the real state machine through a full lifecycle with the
  real notifiers and templates, intercepting only the outbound request. It registers a deliberately
  broken channel and fails if that channel manages to suppress a healthy one.
- **`simulate_endpoints.py`** runs one real check per module and verifies that the fields each
  module depends on still exist upstream. A provider renamed its fields once and a module stayed
  blind for months while the suite was green; this is the detector for that.
- **`simulate_alerts.py`** closes the gap between the other two. Reaching the provider is not the
  same as alerting on it: the AWS module read four fields the feed never published, so it reported
  `OK` past three live incidents and would have passed both scripts above. This one takes each
  provider's real payload, injects a degradation faithful to that provider's own shape, runs the
  real module, and drives the result through the real `NotificationManager` to all four channels —
  then feeds the healthy payload back and checks the all-clear fires. The report says what was
  changed, how many channels received each event, and which branch of the state machine each phase
  took.

The last two talk to the internet, so they are **diagnostics, not deterministic gates**. In
`simulate_endpoints.py` a module that fails is retried, and a failure that does not repeat is
reported as transient rather than failing the run. Do not wire either into CI expecting a stable
signal.

`simulate_alerts.py` currently exits non-zero: `aws` and `gcp` alert and never recover
([#49](https://github.com/solvelab/service-checker/issues/49)). That is a real defect the script
found, not a flaky run — the exit code stays red until it is fixed.

## 📐 What the project guarantees
[`openspec/specs/`](openspec/specs) holds one spec per capability — ten of them, one for each
monitor plus the notification lifecycle. They describe the guarantees in verifiable scenarios, which
is what the module READMEs do not: those describe *configuration*, the specs describe *behaviour*.

## 🐳 Docker usage
See [DOCKER.md](DOCKER.md) for GHCR image usage, dev builds (`docker-compose-dev.yml`), and full environment reference.

## 🧯 Troubleshooting
- **No alerts coming through**: verify the module is enabled, the rule keys are set, and the provider
  is actually degraded. Run `python scripts/simulate_endpoints.py .env.example` — it says, per
  module, whether the provider is reachable and whether the fields the module reads still exist.
- **A module reports `OK` while the provider has an incident**: the module may be reading fields the
  provider no longer publishes. That is exactly what the endpoint simulation detects.
- **Telegram messages not delivered**: check bot token, chat ID, and whether the bot has been added
  to the group.
- **Webhook errors**: confirm the endpoint is reachable and accepts JSON, and validate any auth
  header settings. Remember `status` is an open set — a consumer that raises on an unknown value
  will break when a new event type is added.
- **Google Chat messages missing**: a space accepts one request per second, shared by every webhook
  in it. Check the log for a rejected status and raise `GOOGLE_CHAT_MIN_INTERVAL_SECONDS`. The
  webhook URL never appears in logs, by design — it is a credential.
- **Alertmanager alerts flapping**: `ALERTMANAGER_RESOLVE_AFTER_SECONDS` must exceed the gap between
  two sends, which is `NOTIFICATION_REPEAT_MINUTES` plus one check interval. Leave it at `0` to have
  it derived.
- **Too many alerts**: increase `NOTIFICATION_REPEAT_MINUTES` or narrow `*_SERVICE_FILTER`.
- **Alerts stop after a restart**: notification state lives in memory only. A restart re-alerts what
  is still degraded and loses pending recoveries.

## 🔗 Documentation
- Modules: [Steam](app/modules/steam/README.md), [OpenAI](app/modules/openai/README.md), [Claude](app/modules/claude/README.md), [Rockstar](app/modules/rockstar/README.md), [OCI](app/modules/oci/README.md), [GCP](app/modules/gcp/README.md), [AWS](app/modules/aws/README.md), [GitHub](app/modules/github/README.md), [Bitbucket](app/modules/bitbucket/README.md)
- Notifications: [Overview](app/notifications/README.md) · [Telegram](app/notifications/telegram/README.md) · [Webhook](app/notifications/webhook/README.md) · [Google Chat](app/notifications/google_chat/README.md) · [Alertmanager](app/notifications/alertmanager/README.md)
- Behaviour: [openspec/specs](openspec/specs) · Contributing conventions: [AGENTS.md](AGENTS.md)
- Infra: [DOCKER.md](DOCKER.md), [docker-compose.yml](docker-compose.yml)

## 💖 Support the Project

If you find this project useful, consider supporting its development:

[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?business=ZUADM4SZT5DC8&no_recurring=0&item_name=Projetos+desenvolvidos+com+cuidado+e+dedica%C3%A7%C3%A3o.+O+apoio+incentiva+a+continuidade+e+a+evolu%C3%A7%C3%A3o+constante.&currency_code=BRL)

Your donation helps with:
- 🚀 New features and improvements
- 🐛 Bug fixes and maintenance
- 📖 Documentation updates
- ☕ Developer sustainability
