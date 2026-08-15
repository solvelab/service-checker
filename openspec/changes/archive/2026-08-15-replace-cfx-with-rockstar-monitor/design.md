## Context
O Cfx.re foi adquirido pela Rockstar Games e a Statuspage pública (`status.cfx.re`) foi descomissionada. O monitor `cfx` deste projeto consome `https://status.cfx.re/api/v2/summary.json`, que agora retorna 404 — gerando alertas falsos contínuos no cluster. A nova fonte oficial é `https://support.rockstargames.com/servicestatus`, que cobre todos os serviços Rockstar (incluindo FiveM e RedM, antes em Cfx.re) numa página única.

## Goals / Non-Goals
- Goals
  - Restaurar visibilidade de status para FiveM/RedM (cobertura anterior do `cfx`).
  - Estender naturalmente para os demais serviços Rockstar com filtragem por serviço.
  - Manter o contrato de monitor existente (mesmas formas de `MonitorResult`, integração com `NotificationManager` e templates Telegram).
- Non-Goals
  - Manter compatibilidade com o slug `cfx` na config (é breaking change explícito).
  - Implementar scraping HTML genérico — só o(s) endpoint(s) JSON identificado(s) na investigação.
  - Cobrir histórico de incidentes / componentes individuais por serviço (apenas estado agregado por serviço).

## Decisions
- **Decisão**: Tratar como substituição (REMOVED `cfx-monitor` + ADDED `rockstar-monitor`) em vez de RENAMED.
  - **Por quê**: O domínio, schema upstream, semântica de severidade e conjunto de serviços mudam. RENAMED implicaria continuidade que não existe; REMOVED + ADDED comunica corretamente a quebra.
  - **Alternativa considerada**: Manter slug `cfx` apontando para o novo endpoint — rejeitada porque confunde operadores e mascara o fato de que agora cobrimos todos os serviços Rockstar, não só Cfx.
- **Decisão**: Fonte é `https://support.rockstargames.com/servicestatus` (HTML, não JSON).
  - **Investigação realizada**: A página é Next.js server-rendered e expõe os dados em HTML estático com atributos `data-testid` estáveis. Não há endpoint JSON público — confirmado inspecionando a página. Os campos relevantes são:
    - `data-testid="status-hero-heading"` → status geral (ex.: "All services operational" / "There are partial outages")
    - `data-testid="status-hero-metadata"` → timestamp ("As of April 26, 2026 @ 4:24 PM UTC")
    - `data-testid="status-section-heading"` → seções (Grand Theft Auto Online, Red Dead Online, Online Services, Cfx.re)
    - `data-testid="status-item-N"` → 16 itens individuais (nome + texto de status), agrupados pela seção anterior
  - **Strings i18n descobertas** indicam 3 estados upstream: `all_services_operational` (OK), `partial_outage` (degradado), `all_services_down` (outage).
- **Decisão**: Usar `curl_cffi` (impersonação TLS de Chrome) em vez de `httpx`/`requests`.
  - **Por quê**: A página tem WAF com TLS fingerprinting. Validação real do pod do cluster mostrou:
    - `httpx` com headers Chrome completos → `ReadTimeout` após 25s (handshake passa, WAF fecha leitura)
    - `curl_cffi` com `impersonate="chrome124"` → `403`
    - `curl_cffi` com `impersonate="chrome110"` → `200` em ~70-250ms ✓
    - `curl_cffi` com `impersonate="safari17_0"` → `200` em ~70ms ✓
  - **5 requests sequenciais (15s apart) do pod**: 5/5 OK, 16 items consistentes, sem rate-limit aparente.
  - **Implicação**: novo dep `curl_cffi` em `requirements.txt`. Este monitor **não** usa o `httpx.AsyncClient` compartilhado — instancia seu próprio `curl_cffi.requests.Session` e roda a chamada síncrona dentro de `asyncio.to_thread` para não bloquear o loop. Os demais monitores não são afetados.
- **Decisão**: Reusar o padrão `service_filter` já existente em `cfx` para reaproveitar a UX de configuração.
- **Decisão**: Estratégia de classificação OK/ALERT/ERROR com 2 níveis de avaliação:
  1. **Hero-first (rápido)**: se `status-hero-heading` contém "operational" → curto-circuita para OK (com payload completo dos serviços).
  2. **Per-item (filtro)**: se `service_filter` está definido OU o hero indica degradação, percorre `status-item-N` e classifica cada item por substring case-insensitive: contém "operational" → OK; senão → ALERT. Qualquer item filtrado em estado não-operacional → resultado `ALERT`.
  - **Por quê**: Hero é a fonte de verdade agregada da própria Rockstar; per-item dá granularidade quando o operador quer alertar só quando FiveM (e não GTA Online inteiro) cai.

## Risks / Trade-offs
- **Risco**: Atributos `data-testid` mudam (Next.js rebuild da Rockstar).
  - **Mitigação**: Parsing defensivo com fallback `ERROR` quando hero ou items somem; fixture HTML salva em `tests/fixtures/rockstar/` para detectar drift via teste; alerta operacional ao subir taxa de `ERROR`.
- **Risco**: WAF passa a bloquear `chrome110`/`safari17_0` no futuro.
  - **Mitigação**: `IMPERSONATE_PROFILE` configurável via env; documentar lista de profiles disponíveis. Trocar profile não exige redeploy de imagem se vier de env.
- **Risco**: `curl_cffi` adiciona dep nativa (libcurl-impersonate) na imagem.
  - **Mitigação**: já validado funcionando no pod atual via `pip install`. Adicionar ao `requirements.txt` e garantir build da imagem ainda passa.
- **Risco**: Latência maior que `cfx` antigo (HTML 88KB).
  - **Mitigação**: validado ~70-250ms da rede do pod; `timeout_seconds` default 15s.

## Migration Plan
1. Merge da PR adicionando `rockstar` e removendo `cfx`.
2. No cluster (`monitoring/de-service-checker`): atualizar ConfigMap removendo a entrada `cfx` e adicionando `rockstar` (preservando intervalo/filtro equivalente).
3. `kubectl rollout restart deploy/de-service-checker -n monitoring`.
4. Validar logs: ausência de `module_id=cfx` e `status=OK` em `module_id=rockstar`.
5. Rollback: reverter PR e ConfigMap; aceitar que o monitor antigo continuará em `ERROR` 404.

## Open Questions
- (Resolvido) Não há endpoint JSON; parsing de HTML via `data-testid` é a abordagem suportada.
- (Resolvido) `updated_at` por serviço não é exposto — apenas timestamp global no hero (`status-hero-metadata`).
- (Resolvido) Sem rate-limit aparente em 5 requests/2min; default 60s é seguro.
