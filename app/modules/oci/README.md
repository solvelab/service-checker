# ☁️ OCI Status Module
![Module](https://img.shields.io/badge/Module-OCI-1F6FEB)
![Source](https://img.shields.io/badge/Source-ocistatus.oraclecloud.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🧭 Cfx](../cfx/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitors Oracle Cloud status at https://ocistatus.oraclecloud.com via the RSS feed `incident-summary.rss`, focused on LAD regions.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- GETs the incident RSS feed, extracts the current status from each item, and applies the configured rule.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per feed item (region/service), with independent ALERT/RESOLVED.
- Region/zone filter via `OCI_SERVICE_FILTER` (case-insensitive).

## 🔧 Environment variables (`OCI_`)
- `URL` (default `https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/oci`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, target states (default `investigating,identified,monitoring`); for `keyword`/`regex`, a term or pattern
- `SERVICE_FILTER`: regions/zones to monitor (default "Brazil East (Sao Paulo),Brazil Southeast (Vinhedo)"); empty = all

## 🚦 `status` rule
- Reads the first `<strong>` in each item description as the current status (e.g., `Investigating`, `Identified`, `Monitoring`, `Resolved`).
- Raises ALERT if any filtered region has a status listed in `RULE_VALUE`.
- Payload returns all evaluated incidents or only those that triggered ALERT.

## 🌎 Default monitored regions
- Brazil East (Sao Paulo)
- Brazil Southeast (Vinhedo)

💡 If you need another region/service, capture the exact name shown in the feed title and add it to `OCI_SERVICE_FILTER` (comma-separated). Quick listing for recent regions:
```bash
curl -s https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss \
  | rg '<title>' | sed 's/.*<title>\(.*\)<\/title>.*/\1/' \
  | cut -d '|' -f2 | sed 's/^ *//;s/ *$//' | sort -u
```

## ⚡ Quick examples
- Monitor only LAD (default):
  - `OCI_RULE_KIND=status`
  - `OCI_RULE_VALUE=investigating,identified,monitoring`
  - `OCI_SERVICE_FILTER="Brazil East (Sao Paulo),Brazil Southeast (Vinhedo)"`
- Monitor any active incident in North America:
  - `OCI_RULE_KIND=status`
  - `OCI_SERVICE_FILTER="us east,us west,canada"`
- Search for a specific pattern in the feed:
  - `OCI_RULE_KIND=keyword`
  - `OCI_RULE_VALUE=maintenance`
