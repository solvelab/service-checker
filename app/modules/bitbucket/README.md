# 🪣 Bitbucket Status Module
![Module](https://img.shields.io/badge/Module-Bitbucket-2684FF)
![Source](https://img.shields.io/badge/Source-bitbucket.status.atlassian.com-0052CC)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🐙 GitHub](../github/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://bitbucket.status.atlassian.com using the JSON endpoint `api/v2/summary.json` (Atlassian Statuspage v2).

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload includes evaluated components (or only filtered ones).
- When an alert is detected, enriches the reason with active incidents and scheduled maintenances (fetched from additional endpoints).

## 🔧 Environment variables (`BITBUCKET_`)
- `URL` (default `https://bitbucket.status.atlassian.com/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/1.0`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids or slugs to monitor (e.g., `git-via-https`, `pipelines`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.

### 📇 Known components (slug → name)
- `website` → Website
- `api` → API
- `authentication-and-user-management` → Authentication and user management
- `git-via-https` → Git via HTTPS
- `git-via-ssh` → Git via SSH
- `webhooks` → Webhooks
- `pipelines` → Pipelines
- `pull-requests-and-code-browsing` → Pull requests and code browsing
- `issues-and-projects` → Issues and projects
- `source-downloads` → Source downloads

💡 Components may evolve. Quick listing of the actual ids/slugs:
```bash
curl -s https://bitbucket.status.atlassian.com/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```
Use the output in `BITBUCKET_SERVICE_FILTER` without changing code.

## 🔍 Incident & maintenance enrichment
When the monitor detects degraded components, it automatically fetches:
- `/api/v2/incidents/unresolved.json` — active incidents (title, status, timestamp)
- `/api/v2/scheduled-maintenances/active.json` — active maintenances (title, status, scheduled time)

These details are appended to the alert reason string for richer context. If these extra fetches fail, the base component alert is still sent normally.

## ⚡ Quick examples
- Monitor only Pipelines and Git via HTTPS for outages:
  - `BITBUCKET_RULE_KIND=status`
  - `BITBUCKET_RULE_VALUE=partial_outage,major_outage`
  - `BITBUCKET_SERVICE_FILTER=pipelines,git-via-https`
- Alert on any degradation including maintenance:
  - `BITBUCKET_RULE_KIND=status`
  - `BITBUCKET_RULE_VALUE=degraded_performance,partial_outage,major_outage,under_maintenance`
- Search for a keyword in the raw JSON:
  - `BITBUCKET_RULE_KIND=keyword`
  - `BITBUCKET_RULE_VALUE=major_outage`
