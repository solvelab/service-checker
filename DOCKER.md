# Docker usage

This guide covers two Docker Compose setups:
- **Release image** (`docker-compose.yml`): runs a published GHCR image.
- **Local build** (`docker-compose-dev.yml`): builds from the local Dockerfile.

> **Onde o manifesto de produção vive.** Não é neste repositório. O deploy do cluster de casa é
> `didevlab/housek8s`, em `02-k8s/app/service-checker/01_deployment.yaml`, sincronizado pelo ArgoCD.
> O `deployment.example.yaml` daqui é referência para quem for fazer o próprio deploy — editá-lo não
> muda nada em cluster nenhum.

## ✅ Prerequisites
- Docker Engine and Docker Compose v2
- A `.env` file at the repo root (start from `.env.example`)

## 📦 Use the published image (recommended)
The release image is published in GitHub Packages (GHCR).

```bash
docker pull ghcr.io/solvelab/service-checker:v2.8.5
```

Start the service using the image:
```bash
docker compose up -d
```

To pin a release, set `SERVICE_MONITOR_IMAGE_TAG` in `.env` to the current release tag (for example, `v2.8.5`):
```bash
SERVICE_MONITOR_IMAGE_TAG=v2.8.5
```

If you forked the repository under a different GitHub owner, set `GHCR_OWNER` in `.env`:
```bash
GHCR_OWNER=your-github-owner
```

## 🛠️ Local build (development)
Use the dev compose file to build locally:
```bash
docker compose -f docker-compose-dev.yml up --build
```

## 🧭 How it works
- Each module runs on a schedule (default 60s) and pulls a provider status source.
- Rules decide when a module emits `ALERT` or `RESOLVED`.
- Notifications are dispatched via Telegram or Webhook when enabled.

## 🧰 Global configuration
These apply across modules:
- `SERVICE_MONITOR_MODULES`: comma-separated list of module slugs to load (default `steam,openai,claude,rockstar,oci,gcp,aws,github,bitbucket`).
- `SERVICE_MONITOR_DEFAULT_INTERVAL_SECONDS`: default polling interval in seconds.
- `SERVICE_MONITOR_DEFAULT_TIMEOUT_SECONDS`: default HTTP timeout in seconds.
- `SERVICE_MONITOR_DEFAULT_USER_AGENT`: default user-agent used by all modules.
- `GOOGLE_CHAT_ENABLED`: `false`. Enables the Google Chat channel.
- `GOOGLE_CHAT_WEBHOOK_URL`: empty. **Credential** — the incoming-webhook URL carries `key` and `token`; store it as a secret, never in the image or in version control.
- `GOOGLE_CHAT_MIN_INTERVAL_SECONDS`: `1.1`. Minimum gap between sends; a Chat space accepts 1 request/second, shared by every webhook in it.
- `GOOGLE_CHAT_THREAD_BY_CHECK`: `true`. Groups an alert and its recovery into one conversation.
- `ALERTMANAGER_ENABLED`: `false`. Enables the Alertmanager channel.
- `ALERTMANAGER_URL`: empty. Base URL only, e.g. `http://alertmanager:9093`; the channel appends `/api/v2/alerts`.
- `ALERTMANAGER_TOKEN` / `ALERTMANAGER_HEADER_NAME`: optional auth header (default header `Authorization`). **Credential** — store as a secret.
- `ALERTMANAGER_EXTRA_LABELS`: empty. Static labels merged into every alert for routing, as `env=prod,cluster=main`. Cannot override the labels that identify the alert.
- `ALERTMANAGER_RESOLVE_AFTER_SECONDS`: `0` (auto). How far ahead `endsAt` sits on a firing alert; auto derives it from the repeat window and the check interval. Read `app/notifications/alertmanager/README.md` before changing it — too small and alerts flap.
- `NOTIFICATION_REPEAT_MINUTES`: minimum minutes between repeated alerts for the same service.
- `NOTIFICATION_ERROR_THRESHOLD`: consecutive failed checks before the monitor reports itself as broken (default `3`, minimum `1`). Raise it to stay quiet about monitoring failures.

## 🔧 Module configuration
Each module supports the same environment shape:
- `<MODULE>_URL`
- `<MODULE>_RULE_KIND` (`status` | `keyword` | `regex`)
- `<MODULE>_RULE_VALUE` (rule target values)
- `<MODULE>_SERVICE_FILTER` (comma-separated IDs/slugs; empty = all)
- `<MODULE>_ENABLED` (`true` | `false`)

### Default module values
**Steam (`STEAM_`)**
- `STEAM_URL`: `https://steamstat.us/`
- `STEAM_RULE_KIND`: `status`
- `STEAM_RULE_VALUE`: `major,minor`
- `STEAM_SERVICE_FILTER`: empty (all)
- `STEAM_IMPERSONATE_PROFILE`: `chrome124` (used by `curl_cffi` to bypass Cloudflare TLS fingerprinting; `chrome110`/`chrome116` are refused)

**OpenAI (`OPENAI_`)**
- `OPENAI_URL`: `https://status.openai.com/api/v2/summary.json`
- `OPENAI_RULE_KIND`: `status`
- `OPENAI_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `OPENAI_SERVICE_FILTER`: empty (all)

**Claude (`CLAUDE_`)**
- `CLAUDE_URL`: `https://status.claude.com/api/v2/summary.json`
- `CLAUDE_RULE_KIND`: `status`
- `CLAUDE_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `CLAUDE_SERVICE_FILTER`: empty (all)

**Rockstar (`ROCKSTAR_`)**
- `ROCKSTAR_URL`: `https://support.rockstargames.com/servicestatus`
- `ROCKSTAR_SERVICE_FILTER`: empty (all). Match by item id, name, or section. Example: `FiveM,RedM` or `Cfx.re`.
- `ROCKSTAR_IMPERSONATE_PROFILE`: `chrome124` (used by `curl_cffi` to bypass WAF TLS fingerprinting)
- Note: `ROCKSTAR_RULE_*` and `ROCKSTAR_USER_AGENT` are inert — the monitor uses HTML parsing and `curl_cffi` impersonation.

**OCI (`OCI_`)**
- `OCI_URL`: `https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss`
- `OCI_RULE_KIND`: `status`
- `OCI_RULE_VALUE`: `investigating,identified,monitoring`
- `OCI_SERVICE_FILTER`: `Brazil East (Sao Paulo),Brazil Southeast (Vinhedo)`

**GCP (`GCP_`)**
- `GCP_URL`: `https://status.cloud.google.com/incidents.json`
- `GCP_RULE_KIND`: `status`
- `GCP_RULE_VALUE`: `service_disruption,service_outage,service_information`
- `GCP_SERVICE_FILTER`: `southamerica-east1,us-central1,us-east1`

**AWS (`AWS_`)**
- `AWS_URL`: `https://health.aws.amazon.com/public/currentevents`
- `AWS_RULE_KIND`: `status`
- `AWS_RULE_VALUE`: `operational_issue`
- `AWS_SERVICE_FILTER`: `sa-east-1,us-east-1,us-east-2`

**GitHub (`GITHUB_`)**
- `GITHUB_URL`: `https://www.githubstatus.com/api/v2/summary.json`
- `GITHUB_RULE_KIND`: `status`
- `GITHUB_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `GITHUB_SERVICE_FILTER`: empty (all)

**Bitbucket (`BITBUCKET_`)**
- `BITBUCKET_URL`: `https://bitbucket.status.atlassian.com/api/v2/summary.json`
- `BITBUCKET_RULE_KIND`: `status`
- `BITBUCKET_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `BITBUCKET_SERVICE_FILTER`: empty (all). Example: `pipelines,git-via-https`.

**Cloudflare (`CLOUDFLARE_`)**
- `CLOUDFLARE_URL`: `https://www.cloudflarestatus.com/api/v2/summary.json`
- `CLOUDFLARE_RULE_KIND`: `status`
- `CLOUDFLARE_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `CLOUDFLARE_SERVICE_FILTER`: **not** empty-means-all. Cloudflare publishes 475 components and
  the ones that flap are the data centers, so leaving this empty falls back to a curated
  allowlist: `Tunnel,Authoritative DNS,Network,CDN/Cache,SSL Certificate Provisioning`.
  Set `*` to watch all 475 and expect dozens of alerts a day.
  A component name containing a comma must be quoted — every PoP has one:
  `CLOUDFLARE_SERVICE_FILTER='Tunnel,"Arica, Chile - (ARI)"'`. Applies to every
  module's `SERVICE_FILTER`.

## 🔁 Retry on impersonated fetches

`steam` and `rockstar` read HTML from behind Cloudflare and need `curl_cffi` to imitate a browser's
TLS handshake. Even with the right fingerprint the edge refuses **some** requests — measured at ~30%
on `steam`, and 83% of those succeed on the next attempt. Three refusals in a row would otherwise
trip the dead-monitor notification on noise alone.

- `<SLUG>_FETCH_ATTEMPTS` (default `3`): total attempts, first one included. `1` disables retrying.
- `<SLUG>_FETCH_BACKOFF_SECONDS` (default `1.5`): pause between attempts. `0` is valid.

Only transient failures are retried: `403`, `408`, `429` and `5xx`, plus network errors. A `404` or
an empty body is not — insisting there only delays the diagnosis. Every extra attempt is logged as
`fetch_retry`, so the degradation stays visible instead of being smoothed away.

The worst case must fit the check interval: with `timeout=15s`, three attempts and a 1.5s pause it
is ~48s against a 60s interval. Raise the timeout or the attempts and the checks start overlapping.

## 💾 Alert state across restarts

- `NOTIFICATION_STATE_PATH`: file where pending alerts are kept. **Empty means in-memory
  only**, which is the previous behaviour — and it means an incident that spans a restart
  never gets its all-clear, because no provider announces the same degradation twice.
- `NOTIFICATION_STATE_MAX_AGE_MINUTES` (default `1440`): an alert older than this is
  discarded at startup instead of producing a late, surprising resolution. `0` = no limit.

The path must live on storage that outlives the container. The compose files mount a
named volume at `/var/lib/service-checker` for exactly this. On Kubernetes an `emptyDir`
does **not** work — it disappears with the pod, which is the case this exists to survive.

Writes are atomic (temp file + rename) and skipped when nothing changed, so a healthy
fleet costs no disk traffic. Every failure — unreadable file, bad JSON, full disk — is
logged and swallowed: a monitor that will not start because it could not persist
bookkeeping is worse than one that forgets.

## 🔔 Notifications
**Telegram**
- `TELEGRAM_ENABLED` (default `false`)
- `TELEGRAM_BOT_TOKEN` (required when enabled)
- `TELEGRAM_CHAT_ID` (single chat/group)
- `TELEGRAM_CHAT_IDS` (comma-separated list for multiple chats/groups)
- `TELEGRAM_API_URL` (default `https://api.telegram.org`)

**Webhook**
- `WEBHOOK_ENABLED` (default `false`)
- `WEBHOOK_URL` (required when enabled)
- `WEBHOOK_TOKEN` (optional)
- `WEBHOOK_HEADER_NAME` (default `Authorization`)

## 🧪 Simulate an alert
To force a local alert using Steam:
1. Set `STEAM_RULE_KIND=keyword` and `STEAM_RULE_VALUE=.*` (regex that always matches).
2. Restart the stack:
   ```bash
   docker compose -f docker-compose-dev.yml up --build
   ```
3. Watch logs for the `ALERT` event.

## 🧯 Troubleshooting
- **No logs**: ensure the container is running and check `docker compose ps`.
- **No alerts**: confirm the module is enabled and the rule values are valid.
- **Telegram not sending**: verify token and chat ID; add the bot to the group.
- **Webhook failures**: confirm the endpoint is reachable and accepts JSON.
