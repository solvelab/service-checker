# ☁️ AWS Status Module
![Module](https://img.shields.io/badge/Module-AWS-1F6FEB)
![Source](https://img.shields.io/badge/Source-health.aws.amazon.com-0A66C2)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitors public AWS Health Dashboard events at `https://health.aws.amazon.com/public/currentevents`, focused on specific regions.

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## 🧭 Overview
- Queries `currentevents` (JSON). Everything the endpoint returns is a currently active
  event — it exposes no end timestamp and carries no resolved entries.
- Alert/resolution lifecycle is per event, keyed on the unique id inside the event ARN.
- Region filter via `AWS_SERVICE_FILTER`, matching region code, human region name or service code.

## 🗺️ Where the fields actually live
The public feed does **not** expose `region`, `typeCode`, `startTime` or `endTime` as top-level
fields. Everything the rule engine needs is encoded in the event ARN:

```
arn:aws:health:eu-central-1::event/DIRECTCONNECT/AWS_DIRECTCONNECT_OPERATIONAL_ISSUE/AWS_DIRECTCONNECT_OPERATIONAL_ISSUE_E23AA_ADAB9D3D11F
               └── region ──┘                    └────────── type code ───────────┘ └─────── unique event id ───────┘
```

Payload verified against the live endpoint on **2026-08-15**. Fields present per event:

| Field | Example | Used for |
|---|---|---|
| `arn` | `arn:aws:health:eu-central-1::event/...` | region, type code, stable id |
| `service` | `directconnect-eu-central-1` | region fallback, filtering |
| `service_name` | `AWS Direct Connect` | notification text |
| `region_name` | `Frankfurt` | notification text, filtering |
| `summary` | `Increased Packet loss` | notification text |
| `status` | `"1"` | **metadata only** — see below |
| `date` | `1786765379` | event start (epoch) |

> ⚠️ **`status` is not used to decide whether to alert.** It arrives as a numeric string; `1`, `2`
> and `3` were observed, and higher values appear to mean worse, but AWS documents none of it. The
> module carries it through as metadata and decides purely on presence in the feed plus the type
> code. If AWS ever documents the scale, that is the moment to use it.

Run `python scripts/simulate_endpoints.py` to check that these fields still exist upstream. The
module was silently blind for a long time because nothing verified this.

## 🔧 Environment variables (`AWS_`)
- `URL` (default `https://health.aws.amazon.com/public/currentevents`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/aws`)
- `ENABLED`: `true/false` to enable/disable the module (default `true`)
- `RULE_KIND`: only `status` is implemented for this module (`keyword` and `regex` are **not**
  supported here, unlike the Statuspage-based modules)
- `RULE_VALUE`: tokens matched against the ARN type code, case-insensitive (default
  `operational_issue`, which matches `AWS_*_OPERATIONAL_ISSUE`)
- `SERVICE_FILTER`: regions to monitor (default `sa-east-1,us-east-1,us-east-2`); empty = all

## 🚦 `status` rule
- Every event in the feed is active; there is nothing to filter by end time.
- The region filter matches, case-insensitively, against the region code (`eu-central-1`), the human
  region name (`Frankfurt`) or the service code (`directconnect-eu-central-1`).
- The ARN type code is matched against the tokens in `RULE_VALUE`.
- ALERT if any surviving event remains; the reason lists region, service and summary.
- No event matching your regions is `OK` with an empty payload — good news, not a misconfiguration.

## 📇 Default monitored regions
- sa-east-1 (Sao Paulo)
- us-east-1 (N. Virginia)
- us-east-2 (Ohio)

💡 To discover the regions currently carrying events:
```bash
curl -s https://health.aws.amazon.com/public/currentevents \
  | jq -r '.[] | "\(.region_name)\t\(.arn | split(":")[3])"' | sort -u
```

## ⚡ Quick examples
- Default (latam/east US):
  - `AWS_RULE_KIND=status`
  - `AWS_RULE_VALUE=operational_issue`
  - `AWS_SERVICE_FILTER=sa-east-1,us-east-1,us-east-2`
- Monitor only us-east-1:
  - `AWS_SERVICE_FILTER=us-east-1`
- Monitor by human region name:
  - `AWS_SERVICE_FILTER=Frankfurt,Ireland`
- Catch every event type, not just operational issues:
  - `AWS_RULE_VALUE=aws`
