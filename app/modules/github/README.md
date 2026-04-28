# 🐙 GitHub Status Module
![Module](https://img.shields.io/badge/Module-GitHub-1F6FEB)
![Source](https://img.shields.io/badge/Source-githubstatus.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://www.githubstatus.com using the JSON endpoint `api/v2/summary.json`.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload includes evaluated components (or only filtered ones).
- When an alert is detected, enriches the reason with active incidents and scheduled maintenances (fetched from additional endpoints).

## 🔧 Environment variables (`GITHUB_`)
- `URL` (default `https://www.githubstatus.com/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/github`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids or slugs to monitor (e.g., `actions`, `api-requests`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.

### 📇 Known components (slug → name)
- `git-operations` → Git Operations
- `api-requests` → API Requests
- `actions` → Actions
- `packages` → Packages
- `pages` → Pages
- `codespaces` → Codespaces
- `copilot` → Copilot
- `issues` → Issues
- `pull-requests` → Pull Requests

💡 If a new component appears, use the `id` or generate the slug from the name (lowercase and hyphens). Quick listing:
```bash
curl -s https://www.githubstatus.com/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```
Use the output in `GITHUB_SERVICE_FILTER` without changing code.

## 🔍 Incident & maintenance enrichment
When the monitor detects degraded components, it automatically fetches:
- `/api/v2/incidents/unresolved.json` — active incidents (title, status, timestamp)
- `/api/v2/scheduled-maintenances/active.json` — active maintenances (title, status, scheduled time)

These details are appended to the alert reason string for richer context. If these extra fetches fail, the base component alert is still sent normally.

## ⚡ Quick examples
- Monitor only Actions and Copilot for outages:
  - `GITHUB_RULE_KIND=status`
  - `GITHUB_RULE_VALUE=partial_outage,major_outage`
  - `GITHUB_SERVICE_FILTER=actions,copilot`
- Alert on any degradation including maintenance:
  - `GITHUB_RULE_KIND=status`
  - `GITHUB_RULE_VALUE=degraded_performance,partial_outage,major_outage,under_maintenance`
- Search for a keyword in the raw JSON:
  - `GITHUB_RULE_KIND=keyword`
  - `GITHUB_RULE_VALUE=major_outage`
