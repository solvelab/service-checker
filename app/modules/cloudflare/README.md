# 🟠 Cloudflare Status Module
![Module](https://img.shields.io/badge/Module-Cloudflare-F38020)
![Source](https://img.shields.io/badge/Source-cloudflarestatus.com-F6821F)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [🐙 GitHub](../github/README.md) · [🪣 Bitbucket](../bitbucket/README.md) · [🎮 Rockstar](../rockstar/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🔔 Notifications](../../notifications/README.md) · [💬 Google Chat](../../notifications/google_chat/README.md) · [🔥 Alertmanager](../../notifications/alertmanager/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor https://www.cloudflarestatus.com using the JSON endpoint `api/v2/summary.json` (Statuspage v2).

## 📚 Main docs
- General README: [../../../README.md](../../../README.md)
- Docker: [../../../DOCKER.md](../../../DOCKER.md)

## ⚠️ Read this before changing the filter

Cloudflare publishes **475 components** — twenty times what the other Statuspage modules
carry. 128 are products (`Tunnel`, `Workers`, `R2`, `Access`, `Authoritative DNS`…) and the
rest is one component per point of presence, meaning every city with a data center.

The PoPs are noisy by design. On the day this module was written, two readings hours apart
found 33 and then 51 components non-operational — every single one a PoP, never a product.
Cloudflare reroutes around a degraded PoP; that is the network working, not an incident.

So this module is the one place where **an empty `SERVICE_FILTER` does not mean "watch
everything"**. It falls back to a curated allowlist:

| Component | Why it is watched |
|---|---|
| `Tunnel` | exposes FabCost 3D and Garimpo publicly |
| `Authoritative DNS` | resolves our domains |
| `Network` | the backbone the requests cross |
| `CDN/Cache` | the layer that serves them |
| `SSL Certificate Provisioning` | HTTPS on those domains |

To watch all 475 anyway, set `CLOUDFLARE_SERVICE_FILTER=*`. Expect dozens of alerts a day.

## 🧭 Overview
- GETs the summary JSON and evaluates components by status.
- Supported strategies: `status` (default), `keyword`, `regex`.
- Alert/resolution lifecycle is per component (each `id`/`slug` yields independent ALERT/RESOLVED).
- Payload holds the evaluated components; on ALERT it holds only the degraded ones.
- When an alert is detected, enriches the reason with active incidents and scheduled maintenances.
- Logs `watched component not found in status payload` when a name in the allowlist is absent
  upstream — that is how a rename shows up instead of silently shrinking the watchlist.

## 🔧 Environment variables (`CLOUDFLARE_`)

| Variable | Default | Notes |
|---|---|---|
| `CLOUDFLARE_URL` | `https://www.cloudflarestatus.com/api/v2/summary.json` | Statuspage v2 summary |
| `CLOUDFLARE_INTERVAL_SECONDS` | `60` | |
| `CLOUDFLARE_TIMEOUT_SECONDS` | `10` | |
| `CLOUDFLARE_USER_AGENT` | `service-checker/cloudflare` | |
| `CLOUDFLARE_RULE_KIND` | `status` | `status`, `keyword` or `regex` |
| `CLOUDFLARE_RULE_VALUE` | `degraded_performance,partial_outage,major_outage` | `under_maintenance` is deliberately absent |
| `CLOUDFLARE_SERVICE_FILTER` | the curated allowlist above | comma-separated `id`, `slug` or `name`; `*` watches all 475 |
| `CLOUDFLARE_ENABLED` | `true` | |

Add `cloudflare` to `SERVICE_MONITOR_MODULES` for the module to be loaded at all.

## 🧪 Verifying

```bash
pytest tests/test_cloudflare_monitor.py -v
python scripts/simulate_endpoints.py .env.example   # reachability and field contract
python scripts/simulate_alerts.py                   # degradation actually fires an alert
```

The suite runs against `tests/fixtures/cloudflare/summary.json`, the real payload with its
51 non-operational PoPs. The test that matters most is the one asserting that this payload,
under the default configuration, produces `OK`.
