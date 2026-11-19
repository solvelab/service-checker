# Change: Google Chat card says what broke, in local time, for how long

## Why
Issue #142. Em 2026-11-19 a OpenAI publicou "Elevated error rates for gpt-6 Luna API requests" (16:01:12Z–16:58:04Z), e os cartões do Google Chat diziam só `openai` / `Responses: partial_outage` / `Message: service degraded` / `When: 2026-11-19T16:01:22.923036+00:00`. Não dava para saber o que caiu, a hora vinha em UTC cru, e nem a repetição nem a recuperação diziam há quanto tempo o problema durava: `AlertState` guarda só `last_alert_at`, que é o throttle.

## What Changes
- **Início do problema.** `AlertState` ganha `opened_at`, gravado no primeiro alerta de uma chave, mantido nas repetições, persistido no state store (estado antigo sem o campo usa `last_alert_at`). `MonitorResult` ganha `started_at`, preenchido pelo manager no alerta, na repetição e na recuperação.
- **Incidente da página no item.** Os módulos Statuspage/incident.io (`openai`, `claude`, `github`, `bitbucket`, `cloudflare`, `retell`) anexam a cada componente em alerta os títulos dos incidentes abertos (`incidents`, até 3): os que citam o componente, ou todos quando o incidente não lista componentes (incident.io). A fonte é `incidents/unresolved.json`, com recuo para `incidents.json` filtrado quando a página responde 404 (incident.io), buscada só em ALERT. Falha na busca não muda o alerta.
- **Cartão do Google Chat.**
  - Título `🚨 <Provedor> · <componente> — <Estado legível>` (por exemplo `OpenAI · Responses — Partial outage`); recuperação `✅ ... — Recovered`.
  - Incidentes da página no lugar de `Message: service degraded`; link para a página de status.
  - Horas em `GOOGLE_CHAT_TIMEZONE` (default `America/Sao_Paulo`), `19/11 13:01 (UTC-3)`, sem microssegundos.
  - Repetição: `Em andamento há 17 min (desde 13:01)` e a mudança de estado quando houver (`Partial outage → Degraded performance`). Recuperação: `Duração: 57 min (13:01 → 13:58)`.
  - Thread por componente inalterada.
- **Webhook.** `started_at` e `duration_seconds` (só na recuperação) no corpo, aditivos.

## Impact
- Affected specs: `notifications`.
- Affected code: `app/core/types.py`, `app/core/notifications.py`, `app/core/state_store.py`, `app/core/statuspage.py`, `app/core/config.py`, `app/notifications/google_chat/notifier.py`, `app/notifications/webhook/notifier.py`, os 6 módulos Statuspage, docs.
