# 🔊 ElevenLabs Status Module
![Module](https://img.shields.io/badge/Module-ElevenLabs-000000)
![Source](https://img.shields.io/badge/Source-status.elevenlabs.io-0052CC)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🐙 GitHub](../github/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🟠 Cloudflare](../cloudflare/README.md) · [📞 Retell](../retell/README.md) · [🔔 Notifications](../../notifications/README.md) · [💬 Google Chat](../../notifications/google_chat/README.md) · [🔥 Alertmanager](../../notifications/alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://status.elevenlabs.io using `api/v2/summary.json`. The page runs on incident.io, which serves a Statuspage-compatible summary — with an **empty `components` list** and no `incidents/unresolved.json` (404). A Statuspage module would sit in ERROR on every check, so this one reads what the page does publish: the page indicator and the open incidents.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- Items, each with its own alert/recovery lifecycle:
  - `page` — present exactly while `status.indicator` reaches the threshold;
  - `incident:<id>` — one per open incident, present until it closes, filtered by the incident's own `impact` (never by the indicator), so an incident is not announced restored just because the page indicator moved.
- An empty or unknown indicator (anything but `none`, `minor`, `major`, `critical`, `maintenance`) is an ERROR, not OK.
- If the page ever publishes components, a degraded one (`degraded_performance`, `partial_outage`, `major_outage`) is an item too, restricted by `SERVICE_FILTER`.
- A payload without `status.indicator` is an ERROR: the source changed shape. An empty `components` is not.

## 🔧 Environment variables (`ELEVENLABS_`)
- `URL` (default `https://status.elevenlabs.io/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/1.0`)
- `ENABLED`: `true/false` (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, the **minimum severity** (page indicator and incident impact) that alerts, `minor` < `major` < `critical`. The lowest severity listed wins, so the generic default `major,minor` means "minor and up"; a value naming no severity also reads as `minor`. A non-severity indicator (e.g. `maintenance`) alerts only when listed explicitly.
- `SERVICE_FILTER`: component ids, slugs or names; used only if components appear.

## ⚡ Quick examples
- Everything the page flags (default): `ELEVENLABS_RULE_VALUE=minor`
- Only major outages: `ELEVENLABS_RULE_VALUE=major`
- Inspect the live page:
```bash
curl -s https://status.elevenlabs.io/api/v2/summary.json | jq '{status, incidents: [.incidents[] | {name, status, impact}]}'
```
