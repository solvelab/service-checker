# 🤖 OpenAI Status Module
![Module](https://img.shields.io/badge/Module-OpenAI-1F6FEB)
![Source](https://img.shields.io/badge/Source-status.openai.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🟣 Claude](../claude/README.md) · [🧭 Cfx](../cfx/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://status.openai.com using the JSON endpoint `api/v2/summary.json`.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload includes evaluated components (or only filtered ones).

## 🔧 Environment variables (`OPENAI_`)
- `URL` (default `https://status.openai.com/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/openai`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (e.g., `degraded_performance,partial_outage,major_outage`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: component ids or slugs to monitor (e.g., `chat-completions`, `image-generation`, `login`); empty = all

## 🚦 `status` rule
- Uses the Statuspage states (`operational`, `degraded_performance`, `partial_outage`, `major_outage`, `under_maintenance`).
- Raises ALERT if any filtered component has a status listed in `RULE_VALUE`.

### 📇 Known components (slug → name)
- `video-viewing` → Video viewing
- `embeddings` → Embeddings
- `video-generation` → Video generation
- `image-generation` → Image Generation
- `login` → Login
- `realtime` → Realtime
- `audio` → Audio
- `images` → Images
- `feed` → Feed
- `chat-completions` → Chat Completions
- `responses` → Responses
- `sora` → Sora
- `files` → Files
- `batch` → Batch
- `fine-tuning` → Fine-tuning
- `moderations` → Moderations
- `codex` → Codex
- `gpts` → GPTs
- `agent` → Agent
- `search` → Search
- `deep-research` → Deep Research
- `voice-mode` → Voice mode
- `chatgpt-atlas` → ChatGPT Atlas

💡 If a new component appears, use the `id` or generate the slug from the name (lowercase and hyphens). Quick listing:
```bash
curl -s https://status.openai.com/api/v2/summary.json | jq -r '.components[] | [.id, (.name|ascii_downcase|gsub("[^a-z0-9]+";"-"))] | @tsv'
```
Use the output in `OPENAI_SERVICE_FILTER` without changing code.

## ⚡ Quick examples
- Monitor only Chat Completions and Responses for major outages:
  - `OPENAI_RULE_KIND=status`
  - `OPENAI_RULE_VALUE=major_outage,partial_outage`
  - `OPENAI_SERVICE_FILTER=chat-completions,responses`
- Search for a JSON pattern:
  - `OPENAI_RULE_KIND=regex`
  - `OPENAI_RULE_VALUE=error`
