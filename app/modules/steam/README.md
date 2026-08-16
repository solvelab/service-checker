# 🎮 Steam Module
![Module](https://img.shields.io/badge/Module-Steam-1F6FEB)
![Source](https://img.shields.io/badge/Source-steamstat.us-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🔔 Notifications](../../notifications/README.md) · [💬 Google Chat](../../notifications/google_chat/README.md) · [🔥 Alertmanager](../../notifications/alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://steamstat.us/ with environment-configurable rules and service filtering.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- Fetches the page HTML through `curl_cffi` TLS impersonation and applies the rule defined in env.
- Supports three strategies: `status`, `keyword`, `regex`.
- Result includes a payload with evaluated services for auditing.
- Alert/resolution lifecycle is per service (each Steam Services `id` yields independent ALERT/RESOLVED).
- The `pageviews` entry is ignored to avoid false alerts on traffic metrics.

## 🛡️ Transport: why `curl_cffi` and not `httpx`
`steamstat.us` is fronted by Cloudflare, which rejects on **TLS fingerprint**, not on headers — a
plain `httpx` request gets `403` no matter what `User-Agent` it sends. The module therefore fetches
through `curl_cffi`, impersonating a real browser's TLS stack, the same way the Rockstar module does.

Impersonation profiles age out. Measured against the live endpoint on **2026-08-15**:

| Profile | Result |
|---|---|
| `chrome110`, `chrome116` | ❌ HTTP 403 |
| `chrome119`, `chrome120`, `chrome123`, `chrome124` | ✅ HTTP 200 |
| `safari17_0` | ✅ HTTP 200 |

Default is `chrome124` — a pinned concrete profile rather than the floating `chrome` alias, so a
`curl_cffi` upgrade cannot silently change which fingerprint is sent. When Cloudflare eventually
refuses it too, the symptom is `status=ERROR` with `upstream returned HTTP 403`: raise
`STEAM_IMPERSONATE_PROFILE` to a newer profile and update the table above.

## 🔧 Environment variables (`STEAM_`)
- `URL` (default `https://steamstat.us/`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `IMPERSONATE_PROFILE`: `curl_cffi` browser profile (default `chrome124`) — see the table above
- `USER_AGENT`: **inert for this module** — `curl_cffi` sets its own headers as part of the
  impersonation. Kept for consistency with the other modules.
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target severities (e.g., `major,minor`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: service IDs to monitor (e.g., `store,community,webapi`); empty = all

## 🚦 `status` rule
- Parses the “Steam Services” section and collects id, name, severity (`good`, `minor`, `major`), and text.
- Raises ALERT if any filtered service has a severity listed in `RULE_VALUE`.
- Payload returns the list of evaluated services (or only the filtered ones).

### 📇 Known service IDs
`online`, `ingame`, `store`, `community`, `webapi`, `cms`, `cs2`, `cs_sessions`, `cs_community`, `cs_mm_scheduler`, `deadlock`, `dota2`, `tf2`, `bot`, `database`, `pageviews` (and others that appear on the page).

💡 If a new service appears, grab the `id` shown in the page HTML (`id` attribute on the `<span class="status ...">` element). Quick example:
```bash
curl -s https://steamstat.us/ | rg -o 'status [^"]+" id="([^"]+)"' | sed 's/.*id="//;s/"$//'
```
Use the value in `STEAM_SERVICE_FILTER` without changing code.

## ⚡ Quick examples
- Monitor only Store/Community/Web API for major outages:
  - `STEAM_RULE_KIND=status`
  - `STEAM_RULE_VALUE=major`
  - `STEAM_SERVICE_FILTER=store,community,webapi`
- Specific term:
  - `STEAM_RULE_KIND=keyword`
  - `STEAM_RULE_VALUE=offline`
