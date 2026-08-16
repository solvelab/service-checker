# 🟣 Claude Status Module
![Module](https://img.shields.io/badge/Module-Claude-1F6FEB)
![Source](https://img.shields.io/badge/Source-status.claude.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🔔 Notifications](../../notifications/README.md) · [💬 Google Chat](../../notifications/google_chat/README.md) · [🔥 Alertmanager](../../notifications/alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://status.claude.com using the JSON endpoint `api/v2/summary.json`.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload includes evaluated components (or only filtered ones).

## 🔧 Environment variables (`CLAUDE_`)
- `URL` (default `https://status.claude.com/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/claude`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids or slugs to monitor (e.g., `claude-ai`, `platform-claude-com-formerly-console-anthropic-com`, `claude-api-api-anthropic-com`, `claude-code`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.

### 📇 Known components (slug → name)
- `claude-ai` → claude.ai
- `platform-claude-com-formerly-console-anthropic-com` → platform.claude.com (formerly console.anthropic.com)
- `claude-api-api-anthropic-com` → Claude API (api.anthropic.com)
- `claude-code` → Claude Code

💡 If a new component appears, use the `id` or generate the slug from the name (lowercase and hyphens). Quick listing:
```bash
curl -s https://status.claude.com/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```
Use the output in `CLAUDE_SERVICE_FILTER` without changing code.

## ⚡ Quick examples
- Monitor only Claude API and Claude Code for major outages:
  - `CLAUDE_RULE_KIND=status`
  - `CLAUDE_RULE_VALUE=major_outage,partial_outage`
  - `CLAUDE_SERVICE_FILTER=claude-api-api-anthropic-com,claude-code`
- Search for a JSON pattern:
  - `CLAUDE_RULE_KIND=regex`
  - `CLAUDE_RULE_VALUE=error`
