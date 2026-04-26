# Rockstar Services Monitor

![Source](https://img.shields.io/badge/Source-support.rockstargames.com-FCAF17)

🔗 Nav: [🏠 Home](../../../README.md) · [🎮 Steam](../steam/README.md) · [🤖 OpenAI](../openai/README.md) · [🟣 Claude](../claude/README.md) · [☁️ OCI](../oci/README.md) · [🌐 GCP](../gcp/README.md) · [☁️ AWS](../aws/README.md) · [🐙 GitHub](../github/README.md) · [🔔 Notifications](../../notifications/README.md) · [🐳 Docker](../../../DOCKER.md)

Monitor `https://support.rockstargames.com/servicestatus`, the official status page for all Rockstar Games services — including Cfx.re (FiveM, RedM) which migrated here after the Rockstar acquisition.

## Source

The page is server-rendered HTML (no public JSON endpoint). The monitor parses stable `data-testid` attributes:

- `status-hero-heading` — overall summary (e.g. "All services operational" / "There are partial outages")
- `status-hero-metadata` — timestamp ("As of <date> @ <time> UTC")
- `status-section-heading` — group headings (Grand Theft Auto Online, Red Dead Online, Online Services, Cfx.re)
- `status-item-N` — individual services (PS5, PC, FiveM, RedM, etc.)

## TLS impersonation

The page is fronted by a WAF that fingerprints TLS clients. Plain `httpx` / `requests` from datacenter IPs gets `ReadTimeout`. The monitor uses [`curl_cffi`](https://github.com/lexiforest/curl_cffi) with `impersonate="chrome110"` (default) which mimics a real Chrome TLS handshake. Profile is configurable via `ROCKSTAR_IMPERSONATE_PROFILE`.

## Configuration

- `ROCKSTAR_ENABLED` (default `true`)
- `ROCKSTAR_URL` (default `https://support.rockstargames.com/servicestatus`)
- `ROCKSTAR_INTERVAL_SECONDS` (default inherited)
- `ROCKSTAR_TIMEOUT_SECONDS` (default 15s recommended)
- `ROCKSTAR_USER_AGENT` (unused — `curl_cffi` impersonation provides the UA)
- `ROCKSTAR_SERVICE_FILTER` — optional comma-separated list. Match by item id, item name, or section name. Examples:
  - `FiveM,RedM` — only Cfx.re game services
  - `Cfx.re` — entire Cfx.re section
  - `cfx-re-fivem` — by canonical id
- `ROCKSTAR_IMPERSONATE_PROFILE` (default `chrome110`) — try `safari17_0` if WAF blocks the default

## Severity mapping

| Upstream                                       | App     |
| ---------------------------------------------- | ------- |
| Item text contains "operational"                | OK      |
| Item text contains "outage"/"down"/"issue"     | ALERT   |
| Item text contains "maintenance"               | ALERT   |
| Network/parse failure                          | ERROR   |

When `service_filter` is set, only filtered services count toward severity. Without filter, the hero heading also contributes (any non-operational hero → ALERT).

## Migrating from `cfx`

The old `cfx` monitor consumed `https://status.cfx.re/api/v2/summary.json`, which now returns 404. To preserve coverage:

```diff
- SERVICE_MONITOR_MODULES=...,cfx,...
- CFX_SERVICE_FILTER=cfx-re-platform-server-fxserver
+ SERVICE_MONITOR_MODULES=...,rockstar,...
+ ROCKSTAR_SERVICE_FILTER=FiveM,RedM
```

There is no compatibility shim — the slug `cfx` is removed.
