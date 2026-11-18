# 📞 Retell AI Status Module
![Module](https://img.shields.io/badge/Module-Retell-111111)
![Source](https://img.shields.io/badge/Source-status.retellai.com-0052CC)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🐙 GitHub](../github/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🟠 Cloudflare](../cloudflare/README.md) · [🔔 Notifications](../../notifications/README.md) · [💬 Google Chat](../../notifications/google_chat/README.md) · [🔥 Alertmanager](../../notifications/alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://status.retellai.com using the JSON endpoint `api/v2/summary.json` (Atlassian Statuspage v2). Same shape and enrichment as the Bitbucket module.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- When an alert is detected, enriches the reason with active incidents and scheduled maintenances.

## 🔧 Environment variables (`RETELL_`)
- `URL` (default `https://status.retellai.com/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/1.0`)
- `ENABLED`: `true/false` (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids, slugs or names (e.g., `api,phone-call`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.
- A `RULE_VALUE` that names none of those states — the generic default `major,minor` names page
  indicators — falls back to `degraded_performance,partial_outage,major_outage`, so adding `retell`
  to `SERVICE_MONITOR_MODULES` alone is enough to be alerted.
- Three components are groups whose status follows their children: a degraded `Phone Call` shows up
  together with `End to End Calling`.

### 📇 Known components (slug → name), 2026-11-18
- `api` → API
- `web-call` → Web Call — in End to End Calling
- `alerting` → Alerting — in Features
- `sms-chat` → SMS chat — in Chat
- `end-to-end-calling` → End to End Calling (group)
- `phone-call` → Phone Call — in End to End Calling
- `agent-testing` → Agent Testing — in Features
- `api-chat` → API chat — in Chat
- `analytics` → Analytics — in Features
- `chat` → Chat (group)
- `features` → Features (group)
- `batch-call` → Batch Call — in Features
- `dashboard` → Dashboard
- `qa` → QA — in Features
- `knowledge-base` → Knowledge Base — in Features

💡 Components may evolve. Quick listing of the actual ids/slugs:
```bash
curl -s https://status.retellai.com/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```

## 🔍 Incident & maintenance enrichment
On a degraded component the monitor also fetches `/api/v2/incidents/unresolved.json` and
`/api/v2/scheduled-maintenances/active.json` and appends them to the alert reason. If those fetches
fail, the component alert is still sent.

## ⚡ Quick examples
- Only calling, outages only:
  - `RETELL_RULE_VALUE=partial_outage,major_outage`
  - `RETELL_SERVICE_FILTER=api,phone-call,web-call,batch-call`
- Include maintenance:
  - `RETELL_RULE_VALUE=degraded_performance,partial_outage,major_outage,under_maintenance`
