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
- If the page ever publishes components, a degraded one (`degraded_performance`, `partial_outage`, `major_outage`) is an item too. `SERVICE_FILTER` does not restrict components: it names regions.
- A payload without `status.indicator` is an ERROR: the source changed shape. An empty `components` is not.

## 🔧 Environment variables (`ELEVENLABS_`)
- `URL` (default `https://status.elevenlabs.io/api/v2/summary.json`)
- `INTERVAL_SECONDS` (default 60)
- `TIMEOUT_SECONDS` (default 10)
- `USER_AGENT` (default inherited or `service-checker/1.0`)
- `ENABLED`: `true/false` (default `true`)
- `RULE_KIND`: `status` (default), `keyword`, `regex`
- `RULE_VALUE`: for `status`, the **minimum severity** (page indicator and incident impact) that alerts, `minor` < `major` < `critical`. The lowest severity listed wins, so the generic default `major,minor` means "minor and up"; a value naming no severity also reads as `minor`. A non-severity indicator (e.g. `maintenance`) alerts only when listed explicitly.
- `SERVICE_FILTER`: regions to monitor, by subpath (`united-states`, `eu`, `india`, `singapore`) or name (`EU (Enterprise-only)`), case-insensitive; empty = all. With a filter the `page` item is left out (its indicator aggregates every region), and a value missing from the catalogue is an ERROR naming it.

## 🪧 The page indicator (#146)
The `page` item (raised indicator) carries in `incidents` the titles of the open incidents that
alert in the same check, regions included, so its card says what is degraded instead of only
`ElevenLabs — Minor`.

## 🌍 Regions (#136)
The page has four regional sub-pages and `summary.json` aggregates their incidents without saying which is which. Only incident.io's **undocumented** frontend API maps an incident to its regions:

- `GET /proxy/status.elevenlabs.io` → `subpages[]` (`subpath`, `name`): the catalogue;
- `GET /proxy/status.elevenlabs.io/incidents?subpage_slug=<subpath>` → that region's incidents, with the same ids as `api/v2`.

When an incident is open, the module reads the catalogue and every region's listing (1 + 4 requests) and adds `regions` to the item; the alert reads `Degraded performance of Voice Library voices [EU (Enterprise-only), India (Enterprise-only), Singapore (Enterprise-only)]: investigating`. One incident in several regions stays one item. A healthy page costs no extra request; a filter adds the catalogue (about 4 KB) to every check, to validate it.

If the lookup fails or the shape changes, the alert goes out with the regions already seen, or without any, and the log carries one `WARNING` `region_lookup_failed` per failure streak (`region_lookup_recovered` when it works again). One failing region does not discard the others. Severity and lifecycle always come from `summary.json`; only an incident that reaches `RULE_VALUE` triggers the regional listings.

With a filter, each incident is decided once, the first time it shows up, and the decision holds while it is open. An incident whose region is still unknown (lookup failing, or not yet in any regional listing) is let in — a false positive beats a blind module — and stays in, so it never sends a false "restored" when its region shows up outside the filter or the lookup flaps. Two consequences: with a filter, a raised page indicator with no incident listed stays silent; and the first deploy with a filter while a `page` alert is pending sends its "restored" once.

## ⚡ Quick examples
- Everything the page flags (default): `ELEVENLABS_RULE_VALUE=minor`
- Only major outages: `ELEVENLABS_RULE_VALUE=major`
- Only the Global region: `ELEVENLABS_SERVICE_FILTER=united-states`
- Which regions list an incident:
```bash
for r in united-states eu india singapore; do echo "$r: $(curl -s "https://status.elevenlabs.io/proxy/status.elevenlabs.io/incidents?subpage_slug=$r" | jq -r '[.incidents[] | select(.status != "resolved") | .name] | join(", ")')"; done
```
- Inspect the live page:
```bash
curl -s https://status.elevenlabs.io/api/v2/summary.json | jq '{status, incidents: [.incidents[] | {name, status, impact}]}'
```
