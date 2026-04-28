# ☁️ AWS Status Module
![Module](https://img.shields.io/badge/Module-AWS-1F6FEB)
![Source](https://img.shields.io/badge/Source-health.aws.amazon.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitors public AWS Health Dashboard events at `https://health.aws.amazon.com/public/currentevents`, focused on specific regions.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- Queries `currentevents` (JSON) and evaluates active events (missing `endTime`) in selected regions.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per event/region, with independent ALERT/RESOLVED.
- Region filter via `AWS_SERVICE_FILTER` (ids like `us-east-1`).

## 🔧 Environment variables (`AWS_`)
- `URL` (default `https://health.aws.amazon.com/public/currentevents`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/aws`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, tokens to match against event `typeCode` (default `operational_issue`); for `keyword`/`regex`, a term or pattern applied to the JSON
- `SERVICE_FILTER`: region ids to monitor (default `sa-east-1,us-east-1,us-east-2`); empty = all

## 🚦 `status` rule
- Considers active events (`endTime` missing).
- Matches event `typeCode` against the tokens in `RULE_VALUE` (case-insensitive).
- Raises ALERT if any active event affects a filtered region.

## 📇 Default monitored regions
- sa-east-1 (Sao Paulo)
- us-east-1 (N. Virginia)
- us-east-2 (Ohio)

💡 For other regions, use the id exactly as shown in `region` on the events. Quick listing:
```bash
curl -s https://health.aws.amazon.com/public/events | jq -r '.[].region' | sort -u | head
```

## ⚡ Quick examples
- Default (latam/east US):
  - `AWS_RULE_KIND=status`
  - `AWS_RULE_VALUE=operational_issue`
  - `AWS_SERVICE_FILTER=sa-east-1,us-east-1,us-east-2`
- Monitor only us-east-1:
  - `AWS_SERVICE_FILTER=us-east-1`
- Search for a free-text term:
  - `AWS_RULE_KIND=keyword`
  - `AWS_RULE_VALUE=latency`
