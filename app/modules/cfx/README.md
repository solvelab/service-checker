# 🧭 Cfx Status Module
![Module](https://img.shields.io/badge/Module-Cfx-1F6FEB)
![Source](https://img.shields.io/badge/Source-status.cfx.re-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://status.cfx.re using the JSON endpoint `api/v2/summary.json`.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload includes evaluated components (or only filtered ones).

## 🔧 Environment variables (`CFX_`)
- `URL` (default `https://status.cfx.re/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/cfx`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids or slugs to monitor (e.g., `fivem,redm,keymaster`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.

### 📇 Known components (slug → name)
- `cnl` → CnL (Client authentication)
- `forums` → Forums
- `games` (group) → Games
- `fivem` → FiveM
- `game-services` (group) → Game Services
- `policy` → Policy
- `server-list-frontend` → Server List Frontend
- `redm` → RedM
- `web-services` (group) → Web Services
- `keymaster` → Keymaster
- `runtime` → "Runtime"
- `cfx-re-platform-server-fxserver` → Cfx.re Platform Server (FXServer)
- `idms` → IDMS
- `portal` → Portal

💡 If a new component appears, use the `id` or generate the slug from the name (lowercase and hyphens). Quick listing:
```bash
curl -s https://status.cfx.re/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```
Use the output in `CFX_SERVICE_FILTER` without changing code.

## ⚡ Quick examples
- Monitor only FiveM, RedM, and Keymaster for major outages:
  - `CFX_RULE_KIND=status`
  - `CFX_RULE_VALUE=major_outage,partial_outage`
  - `CFX_SERVICE_FILTER=fivem,redm,keymaster`
- Search for a JSON pattern:
  - `CFX_RULE_KIND=regex`
  - `CFX_RULE_VALUE=error`
