# Docker usage

This guide covers two Docker Compose setups:
- **Release image** (`docker-compose.yml`): runs a published GHCR image.
- **Local build** (`docker-compose-dev.yml`): builds from the local Dockerfile.

> **Onde o manifesto de produção vive.** Não é neste repositório. O deploy do cluster de casa é
> `didevlab/housek8s`, em `02-k8s/app/service-checker/01_deployment.yaml`, sincronizado pelo ArgoCD.
> O `deployment.example.yaml` daqui é referência para quem for fazer o próprio deploy — editá-lo não
> muda nada em cluster nenhum.

## ✅ Prerequisites
- Docker Engine and Docker Compose v2
- A `.env` file at the repo root (start from `.env.example`)

## 📦 Use the published image (recommended)
The release image is published in GitHub Packages (GHCR).

```bash
docker pull ghcr.io/solvelab/service-checker:v2.18.0
```

Start the service using the image:
```bash
docker compose up -d
```

To pin a release, set `SERVICE_MONITOR_IMAGE_TAG` in `.env` to the current release tag (for example, `v2.18.0`):
```bash
SERVICE_MONITOR_IMAGE_TAG=v2.18.0
```

If you forked the repository under a different GitHub owner, set `GHCR_OWNER` in `.env`:
```bash
GHCR_OWNER=your-github-owner
```

## 🛠️ Local build (development)
Use the dev compose file to build locally:
```bash
docker compose -f docker-compose-dev.yml up --build
```

## 🧭 How it works
- Each module runs on a schedule (default 60s) and pulls a provider status source.
- Rules decide when a module emits `ALERT` or `RESOLVED`.
- Notifications are dispatched to every enabled channel: Telegram, Webhook, Google Chat and
  Alertmanager.

## 🧰 Global configuration
These apply across modules:
- `SERVICE_MONITOR_MODULES`: comma-separated list of module slugs to load (default `steam,openai,claude,rockstar,oci,gcp,aws,github,bitbucket,cloudflare,retell,elevenlabs`).
- `SERVICE_MONITOR_DEFAULT_INTERVAL_SECONDS`: default polling interval in seconds.
- `SERVICE_MONITOR_DEFAULT_TIMEOUT_SECONDS`: default HTTP timeout in seconds.
- `SERVICE_MONITOR_DEFAULT_USER_AGENT`: default user-agent used by all modules.
- `GOOGLE_CHAT_ENABLED`: `false`. Enables the Google Chat channel.
- `GOOGLE_CHAT_WEBHOOK_URL`: empty. **Credential** — the incoming-webhook URL carries `key` and `token`; store it as a secret, never in the image or in version control.
- `GOOGLE_CHAT_MIN_INTERVAL_SECONDS`: `1.1`. Minimum gap between sends; a Chat space accepts 1 request/second, shared by every webhook in it.
- `GOOGLE_CHAT_THREAD_BY_CHECK`: `true`. Groups an alert and its recovery into one conversation.
- `GOOGLE_CHAT_TIMEZONE`: `America/Sao_Paulo`. IANA zone for every time on a card; an invalid zone falls back to the default with a warning.
- `ALERTMANAGER_ENABLED`: `false`. Enables the Alertmanager channel.
- `ALERTMANAGER_URL`: empty. Base URL only, e.g. `http://alertmanager:9093`; the channel appends `/api/v2/alerts`.
- `ALERTMANAGER_TOKEN` / `ALERTMANAGER_HEADER_NAME`: optional auth header (default header `Authorization`). **Credential** — store as a secret.
- `ALERTMANAGER_EXTRA_LABELS`: empty. Static labels merged into every alert for routing, as `env=prod,cluster=main`. Cannot override the labels that identify the alert.
- `ALERTMANAGER_RESOLVE_AFTER_SECONDS`: `0` (auto). How far ahead `endsAt` sits on a firing alert; auto derives it from the repeat window and the check interval. Read `app/notifications/alertmanager/README.md` before changing it — too small and alerts flap.
- `NOTIFICATION_REPEAT_MINUTES`: minimum minutes between repeated alerts for the same service.
- `NOTIFICATION_ERROR_THRESHOLD`: consecutive failed checks before the monitor reports itself as broken (default `3`, minimum `1`). Raise it to stay quiet about monitoring failures.
- `NOTIFICATION_RECOVERY_THRESHOLD`: successful checks in a row that end an announced monitoring failure (default `3`, minimum `1`). An error inside that window continues the same failure and is throttled by `NOTIFICATION_REPEAT_MINUTES`, so a flapping provider does not page "failure / restored" on every flap. The "restored" message arrives `N - 1` cycles later than the first success; `1` restores the old behaviour.
- `NOTIFICATION_CONNECTIVITY_QUORUM` (default `0.7`): when this fraction of the loaded modules (minimum 3) is failing as one of them reaches the threshold, the failure is the checker's own network. It is announced once, as the monitoring failure of the module `checker`, and no module pages its own failure while it lasts. `1` requires every module. Out of `(0, 1]` warns and uses the default.
- `NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES` (default `10`): a connectivity loss shorter than this reaches no channel, only the log (`connectivity_lost` / `connectivity_restored`). `0` announces every loss.
- `NOTIFICATION_CONNECTIVITY_WINDOWS` (optional, e.g. `04:00-04:15,05:00-05:10`): windows where a loss is expected, such as a scheduled router reboot. A loss that starts inside one and ends before its end never notifies; one that outlasts it falls back to the tolerance. A single provider failing inside a window still pages. A malformed item is ignored with a `config_fallback` warning; the windows read are listed in the `startup` log.
- `NOTIFICATION_CONNECTIVITY_TIMEZONE` (default `America/Sao_Paulo`): IANA zone the windows are read in.
- `NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES` (default `60`): a failure streak made only of the source's anti-bot challenge (`UpstreamChallenged`, steam and rockstar) is announced only once it lasts this long; a shorter one only logs `monitor_challenge_absorbed`. Any other error in the streak pages at the plain threshold, and a failure already announced keeps repeating. `0` pages a challenge at the threshold. Negative or not a whole number warns and uses the default.

### 📣 Startup announcement and heartbeat (#124)
Silence on a channel used to mean either "every provider is healthy" or "the checker is down". These two messages tell them apart. They go to Telegram, webhook and Google Chat; Alertmanager never gets them.
- `NOTIFICATION_STARTUP_ANNOUNCE` (default `true`): once every loaded module delivered its first result, each channel gets one "online" message with the version, the pod, the channels and each provider's first result (`OK`, `ALERT`, `ERROR` with its reason). A wrong `*_SERVICE_FILTER` shows up here as `ERROR`, not after the failure threshold.
- `NOTIFICATION_STARTUP_TIMEOUT_SECONDS` (default `120`): the announcement does not wait longer than this; a module that has not answered shows as `no result yet`, and `status_report_partial` names it in the log.
- `NOTIFICATION_HEARTBEAT_TIME` (default `09:00`, `HH:MM`): a daily "still running" message with the uptime and each provider's last result. Empty switches it off; a value that is not `HH:MM` warns and uses `09:00`. The first one is the next occurrence after startup — the announcement covers the current day — so a restart never repeats the day's heartbeat.
- `NOTIFICATION_HEARTBEAT_TIMEZONE` (default `America/Sao_Paulo`): IANA zone of the heartbeat time and of the times shown in the reports.

A report no channel accepted is tried again, up to 3 attempts 60 s apart; the log carries `startup_announced` / `heartbeat_sent`, or `status_report_failed` when it gave up. On Telegram the report is always a new message, also in `daily_summary` mode: an edit of the summary does not ring the phone.

## 🔧 Module configuration
Each module supports the same environment shape:
- `<MODULE>_URL`
- `<MODULE>_RULE_KIND` (`status` | `keyword` | `regex`)
- `<MODULE>_RULE_VALUE` (rule target values)
- `<MODULE>_SERVICE_FILTER` (comma-separated IDs/slugs; empty = all)
- `<MODULE>_ENABLED` (`true` | `false`)

### Default module values
**Steam (`STEAM_`)**
- `STEAM_URL`: `https://steamstat.us/`
- `STEAM_RULE_KIND`: `status`
- `STEAM_RULE_VALUE`: `major,minor`
- `STEAM_SERVICE_FILTER`: empty (all)
- `STEAM_IMPERSONATE_PROFILE`: `chrome124` (used by `curl_cffi` to bypass Cloudflare TLS fingerprinting; `chrome110`/`chrome116` are refused)

**OpenAI (`OPENAI_`)**
- `OPENAI_URL`: `https://status.openai.com/api/v2/summary.json`
- `OPENAI_RULE_KIND`: `status`
- `OPENAI_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `OPENAI_SERVICE_FILTER`: empty (all)

**Claude (`CLAUDE_`)**
- `CLAUDE_URL`: `https://status.claude.com/api/v2/summary.json`
- `CLAUDE_RULE_KIND`: `status`
- `CLAUDE_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `CLAUDE_SERVICE_FILTER`: empty (all)

**Rockstar (`ROCKSTAR_`)**
- `ROCKSTAR_URL`: `https://support.rockstargames.com/servicestatus`
- `ROCKSTAR_SERVICE_FILTER`: empty (all). Match by item id, name, or section. Example: `FiveM,RedM` or `Cfx.re`.
- `ROCKSTAR_IMPERSONATE_PROFILE`: `chrome124` (used by `curl_cffi` to bypass WAF TLS fingerprinting)
- Note: `ROCKSTAR_RULE_*` and `ROCKSTAR_USER_AGENT` are inert — the monitor uses HTML parsing and `curl_cffi` impersonation.

**OCI (`OCI_`)**
- `OCI_URL`: `https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss`
- `OCI_RULE_KIND`: `status`
- `OCI_RULE_VALUE`: `investigating,identified,monitoring`
- `OCI_SERVICE_FILTER`: `Brazil East (Sao Paulo),Brazil Southeast (Vinhedo)`

**GCP (`GCP_`)**
- `GCP_URL`: `https://status.cloud.google.com/incidents.json`
- `GCP_RULE_KIND`: `status`
- `GCP_RULE_VALUE`: `service_disruption,service_outage,service_information`
- `GCP_SERVICE_FILTER`: `southamerica-east1,us-central1,us-east1`

**AWS (`AWS_`)**
- `AWS_URL`: `https://health.aws.amazon.com/public/currentevents`
- `AWS_RULE_KIND`: `status`
- `AWS_RULE_VALUE`: `operational_issue`
- `AWS_SERVICE_FILTER`: `sa-east-1,us-east-1,us-east-2`

**GitHub (`GITHUB_`)**
- `GITHUB_URL`: `https://www.githubstatus.com/api/v2/summary.json`
- `GITHUB_RULE_KIND`: `status`
- `GITHUB_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `GITHUB_SERVICE_FILTER`: empty (all)

**Bitbucket (`BITBUCKET_`)**
- `BITBUCKET_URL`: `https://bitbucket.status.atlassian.com/api/v2/summary.json`
- `BITBUCKET_RULE_KIND`: `status`
- `BITBUCKET_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `BITBUCKET_SERVICE_FILTER`: empty (all). Example: `pipelines,git-via-https`.

**Cloudflare (`CLOUDFLARE_`)**
- `CLOUDFLARE_URL`: `https://www.cloudflarestatus.com/api/v2/summary.json`
- `CLOUDFLARE_RULE_KIND`: `status`
- `CLOUDFLARE_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`
- `CLOUDFLARE_SERVICE_FILTER`: **not** empty-means-all. Cloudflare publishes 475 components and
  the ones that flap are the data centers, so leaving this empty falls back to a curated
  allowlist: `Tunnel,Authoritative DNS,Network,CDN/Cache,SSL Certificate Provisioning`.
  Set `*` to watch all 475 and expect dozens of alerts a day.
  A component name containing a comma must be quoted — every PoP has one:
  `CLOUDFLARE_SERVICE_FILTER='Tunnel,"Arica, Chile - (ARI)"'`. Applies to every
  module's `SERVICE_FILTER`.

**Retell AI (`RETELL_`)**
- `RETELL_URL`: `https://status.retellai.com/api/v2/summary.json`
- `RETELL_RULE_KIND`: `status`
- `RETELL_RULE_VALUE`: `degraded_performance,partial_outage,major_outage`. A value naming no
  component status (the generic default `major,minor`) falls back to these three.
- `RETELL_SERVICE_FILTER`: empty (all 15). Example: `api,phone-call,web-call`.

**ElevenLabs (`ELEVENLABS_`)**
- `ELEVENLABS_URL`: `https://status.elevenlabs.io/api/v2/summary.json` (incident.io, no components)
- `ELEVENLABS_RULE_KIND`: `status`
- `ELEVENLABS_RULE_VALUE`: the **minimum severity** — page indicator and each incident's impact — that alerts, `minor` < `major` <
  `critical`. Default `minor`; `major` silences minor incidents. A value naming no severity
  (the generic default `major,minor` included) reads as `minor`.
- `ELEVENLABS_SERVICE_FILTER`: applies only if the page starts publishing components.

## 🔁 Retry on impersonated fetches

`steam` and `rockstar` read HTML from behind Cloudflare and need `curl_cffi` to imitate a browser's
TLS handshake. Even with the right fingerprint the edge refuses **some** requests — measured at ~30%
on `steam`, and 83% of those succeed on the next attempt. Three refusals in a row would otherwise
trip the dead-monitor notification on noise alone.

- `<SLUG>_FETCH_ATTEMPTS` (default `3`): total attempts, first one included. `1` disables retrying.
- `<SLUG>_FETCH_BACKOFF_SECONDS` (default `1.5`): pause between attempts. `0` is valid.

Only transient failures are retried: `403`, `408`, `429` and `5xx`, plus network errors. A `404` or
an empty body is not — insisting there only delays the diagnosis. Every extra attempt is logged as
`fetch_retry`, so the degradation stays visible instead of being smoothed away.

The worst case must fit the check interval: with `timeout=15s`, three attempts and a 1.5s pause it
is ~48s against a 60s interval. Raise the timeout or the attempts and the checks start overlapping.

## 💾 Alert state across restarts

- `NOTIFICATION_STATE_PATH`: file where pending alerts are kept. **Empty means in-memory
  only**, which is the previous behaviour — and it means an incident that spans a restart
  never gets its all-clear, because no provider announces the same degradation twice.
- `NOTIFICATION_STATE_MAX_AGE_MINUTES` (default `1440`): an alert older than this is
  discarded at startup instead of producing a late, surprising resolution. `0` = no limit.

The path must live on storage that outlives the container. The compose files mount a
named volume at `/var/lib/service-checker` for exactly this. On Kubernetes an `emptyDir`
does **not** work — it disappears with the pod, which is the case this exists to survive.

Writes are atomic (temp file + rename) and skipped when nothing changed, so a healthy
fleet costs no disk traffic. Every failure — unreadable file, bad JSON, full disk — is
logged and swallowed: a monitor that will not start because it could not persist
bookkeeping is worse than one that forgets.

## 🪵 Logging

- `SERVICE_MONITOR_LOG_LEVEL` (default `INFO`): nível do logger `service_monitor`, que é o único
  configurado — um `logging.getLogger(__name__)` fora dessa árvore não tem handler e some. Um nome
  de nível inválido não é aceito em silêncio: vira `INFO` **com um aviso**.

A saída é JSON em stdout, uma linha por evento, e só as chaves de `extra` da allowlist do formatter
aparecem. Passar uma chave que não está lá a descarta sem dizer nada — foi assim que
`notification channel failed` deixou de dizer qual canal falhou —, e
`tests/test_logging.py::test_every_extra_key_used_in_the_app_is_allowed` quebra quando isso acontece.

### Log como painel (#125)

Não há dashboard: `kubectl logs` é o painel, então o log separa rotina de novidade.

- **Checagem repetida vai para `DEBUG`.** Um `monitor_check` com o mesmo status e o mesmo motivo do
  anterior não aparece em `INFO`; a primeira checagem e toda mudança de status ou de motivo mantêm o
  nível de sempre (`WARNING` para `ALERT`, `ERROR` para `ERROR`). Um erro que se repete também desce
  para `DEBUG` depois da primeira linha — o quadro abaixo continua mostrando o módulo como `ERROR`.
  Os canais de notificação não mudam: decidem pelo status, não pelo nível da linha. A chave da
  repetição é status, motivo e mensagem — e, numa exceção, o tipo dela —, então um `ReadTimeout`
  depois de um `ConnectTimeout` não é tratado como repetição só porque os dois têm texto vazio.
  **Atenção a quem alerta pelo log** (regra do Loki contando `level=ERROR` por janela): uma falha
  persistente agora escreve uma linha `ERROR` e depois só `DEBUG`. O estado que continua visível é
  o quadro (`failing`), e com o periódico desligado (`0`) nada em `INFO` repete a falha.
- **Quadro de status** (`event: status_board`): uma linha por provedor com status, desde quando,
  última checagem e uma nota — o motivo atual, ou o último erro depois que o provedor voltou ao
  normal, para um erro que rolou para fora da tela continuar visível. Sai depois da primeira rodada,
  a cada mudança de status (mudanças a menos de 5 s umas das outras viram um quadro só) e a cada
  `SERVICE_MONITOR_STATUS_BOARD_MINUTES` (default `15`; `0` desliga o periódico; número inteiro).
  Valor negativo ou fracionário é avisado e vira `15`. No rodapé, `failing` conta os módulos em
  `ERROR` (o checker não consegue checar) e `open alerts` os alertas de serviço anunciados e não
  recuperados — um provedor em `ALERT` aparece ali, não em `failing`. Assim `kubectl logs --tail=30` sempre traz um quadro recente.
- `SERVICE_MONITOR_LOG_FORMAT` (default `json`): `json` mantém uma linha JSON por evento — o quadro vai
  como um objeto `board` parseável —; `pretty` escreve linhas alinhadas com carimbo UTC (`Z`) e desenha
  o quadro como tabela de até 100 colunas. Valor desconhecido é avisado e vira `json`. Use `pretty` quando
  quem lê o log é gente; `json` quando é um coletor (Loki, Elastic).

```
2026-11-19 05:17:44Z  INFO   ── status board ─ v2.18.0 ─ up 0m ─────────────────────────────────────
  PROVIDER    STATUS  SINCE        CHECKED   NOTE
  oci         OK      19/11 05:17  05:17:43  —
  openai      OK      19/11 05:17  05:17:43  —
  bitbucket   ERROR   19/11 05:17  05:17:43  no target components matched filter
  channels: telegram   open alerts: 0   failing: 1
```

## 🩺 Liveness probe (#134)

O app não serve HTTP, então o probe do Kubernetes lê a idade de um arquivo. Processo morto já
reinicia o container sozinho; o probe cobre o processo vivo e travado — event loop bloqueado ou um
módulo pendurado.

- `SERVICE_MONITOR_LIVENESS_PATH` (default `/tmp/service-checker-alive`; vazio desliga): uma task
  grava esse arquivo a cada 30 s, **só** quando todo módulo carregado completou uma checagem dentro
  da sua janela — `3 × intervalo + pior caso de uma busca` (3 tentativas do timeout mais o backoff),
  com **piso de 15 minutos**. O piso existe porque o ciclo também espera a entrega das notificações,
  serializada entre módulos (um Telegram fora do ar segura ~4 min por rodada), e restart não conserta
  canal lento — só para o monitoramento durante o CrashLoop. Travamento real é pego em até ~17 min
  (janela + 120 s do probe); o heartbeat diário continua sendo a outra rede. Antes da primeira checagem, a janela conta da subida do processo. O
  módulo atrasado sai num `liveness_stale` (com `modules`), a volta num `liveness_fresh`; falha de
  escrita num `liveness_write_failed`, uma vez por motivo.
- `python -m app.healthcheck [max_age_seconds]` sai `0` quando o arquivo tem menos de `max_age`
  (default `120`) e `1` em qualquer outro caso — arquivo ausente, liveness desligado ou argumento
  inválido —, com o motivo numa linha, que o kubelet guarda no evento do pod.

```yaml
livenessProbe:
  exec:
    command: ["python", "-m", "app.healthcheck"]
  initialDelaySeconds: 60
  periodSeconds: 60
  timeoutSeconds: 5      # o default de 1 s é curto para subir um python
  failureThreshold: 3
readinessProbe:          # o pod não recebe tráfego; existe para quem exige o par
  exec:
    command: ["python", "-m", "app.healthcheck"]
  initialDelaySeconds: 10
  periodSeconds: 60
  timeoutSeconds: 5
```

Um módulo com intervalo longo ganha janela longa sozinho; o `max_age` do probe só precisa passar
dos 30 s da gravação com folga. O arquivo precisa de disco local gravável: com
`readOnlyRootFilesystem: true`, monte um `emptyDir` em `/tmp` (ou aponte o path para um), e não use
volume de rede — a gravação é síncrona no loop. Um bug na própria verificação loga
`liveness_check_failed` e mantém a gravação, para não virar restart em loop. Com o liveness desligado, **não** declare o probe: ele falharia
sempre e o pod entraria em restart.

## 🔔 Notifications
**Telegram**
- `TELEGRAM_TIMESTAMP_FORMAT` (default `%Y-%m-%d %H:%M:%S %Z`): `strftime` do carimbo nas mensagens.
  Uma diretiva que a plataforma não conhece **não** faz o envio falhar — no Linux `strftime` devolve
  o texto literal —, então o módulo detecta que nada foi interpretado, avisa e cai para ISO-8601.
- `TELEGRAM_TIMESTAMP_ZONE` (default `UTC`): `UTC` ou `LOCAL`, esta última usando o fuso do container.
  Vale para os cartões do modo `per_event`.
- `TELEGRAM_MODE` (default `daily_summary`): `daily_summary` mantém **uma mensagem por chat por dia**,
  editada a cada evento, que só notifica quando um problema novo começa (issue #100); `per_event` envia
  um cartão por evento, como antes. Valor desconhecido é avisado no boot e vira `daily_summary`.
- `TELEGRAM_SUMMARY_TIMEZONE` (default `America/Sao_Paulo`): fuso IANA que decide onde o dia do resumo
  começa e em que horário ele mostra início e resolução. Fuso inválido é avisado e vira o default.
- `TELEGRAM_ENABLED` (default `false`)
- `TELEGRAM_BOT_TOKEN` (required when enabled)
- `TELEGRAM_CHAT_ID` (single chat/group)
- `TELEGRAM_CHAT_IDS` (comma-separated list for multiple chats/groups)
- `TELEGRAM_API_URL` (default `https://api.telegram.org`)

**Webhook**
- `WEBHOOK_ENABLED` (default `false`)
- `WEBHOOK_URL` (required when enabled)
- `WEBHOOK_TOKEN` (optional)
- `WEBHOOK_HEADER_NAME` (default `Authorization`)

## 🧪 Simulate an alert
To force a local alert using Steam:
1. Set `STEAM_RULE_KIND=keyword` and `STEAM_RULE_VALUE=.*` (regex that always matches).
2. Restart the stack:
   ```bash
   docker compose -f docker-compose-dev.yml up --build
   ```
3. Watch logs for the `ALERT` event.

## 🧯 Troubleshooting
- **No logs**: ensure the container is running and check `docker compose ps`.
- **No alerts**: confirm the module is enabled and the rule values are valid.
- **Telegram not sending**: verify token and chat ID; add the bot to the group.
- **Webhook failures**: confirm the endpoint is reachable and accepts JSON.
