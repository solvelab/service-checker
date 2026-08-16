# Change: Add Cloudflare Status monitor

## Why
O Cloudflare é o único provedor de que dependemos que carrega tráfego de produção nosso e não está monitorado. `openspec/specs/fabcost3d-deployment/spec.md` (repo `fabcost3d`) e `filial/openspec/specs/garimpo-app/spec.md` expõem FabCost 3D e Garimpo publicamente **através do Cloudflare Tunnel**: se o Tunnel cai, os dois somem e hoje ninguém é avisado.

O provedor publica Statuspage v2 em `https://www.cloudflarestatus.com/api/v2/summary.json`, mesma forma já consumida por `github`, `bitbucket`, `openai` e `claude`. O que muda é a escala, e ela muda um default.

Cloudflare publica **475 componentes**: 128 produtos (`Tunnel`, `Workers`, `R2`, `Authoritative DNS`…) e o restante um por ponto de presença, ou seja uma cidade com data center. Duas leituras feitas com horas de diferença encontraram 33 e depois 51 componentes fora de `operational` — todos PoP, nenhum produto. PoP oscilando é a rede funcionando: o Cloudflare reroteia em volta. Nos outros módulos `SERVICE_FILTER` vazio significa "monitorar tudo", leitura segura para treze componentes; aqui produziria dezenas de alertas por dia sem nada que nos afete estar fora do ar, e ensinaria o plantão a ignorar o feed.

## What Changes
- Adicionar módulo `cloudflare` (`app/modules/cloudflare/`) consumindo `api/v2/summary.json`.
- Suportar as três estratégias de regra existentes (`status`, `keyword`, `regex`).
- **Divergir do padrão em um ponto**: `service_filter` vazio cai numa allowlist curada em vez de monitorar tudo. Cada nome foi verificado contra o payload real e existe por um motivo apontável: `Tunnel` (expõe FabCost 3D e Garimpo), `Authoritative DNS` (resolve nossos domínios), `Network` (backbone), `CDN/Cache` (entrega), `SSL Certificate Provisioning` (HTTPS). Escape hatch explícito: `CLOUDFLARE_SERVICE_FILTER=*` monitora os 475.
- Registrar em log quando um nome da allowlist não aparece no payload — é como uma renomeação upstream aparece, em vez de encolher a lista de vigiados em silêncio.
- Enriquecer alertas com incidentes e manutenções, espelhando `bitbucket`.
- Registrar URL default em `app/core/config.py::_default_url`.
- Fiar `cloudflare` em `SERVICE_MONITOR_MODULES` e as oito `CLOUDFLARE_*` no `.env.example`, nos dois compose e no `deployment.yaml`.
- Adicionar entrada em `scripts/simulate_alerts.py` com degradação própria: `_statuspage_degrade` quebra `components[0]`, que aqui é um grupo de continente ou um data center, fora da allowlist — produziria uma execução verde que não prova nada.

## Impact
- Affected specs: `cloudflare-monitor` (criação).
- Affected code:
  - `app/modules/cloudflare/` — novo módulo (`monitor.py`, `__init__.py`, `README.md`)
  - `app/core/config.py` — branch adicional em `_default_url`
  - `app/modules/*/README.md` — link Cloudflare na navegação
  - `tests/test_cloudflare_monitor.py`, `tests/fixtures/cloudflare/summary.json` — nova suíte e payload real
  - `scripts/simulate_alerts.py` — degradação e entrada na tabela de provedores
  - `.env.example`, `docker-compose.yml`, `docker-compose-dev.yml`, `deployment.yaml`, `README.md`, `DOCKER.md`
- Não-breaking para os outros módulos: nenhum comportamento compartilhado muda. A divergência do `service_filter` vazio é local ao módulo `cloudflare` e está documentada no README dele e no `DOCKER.md`.
- Sem novas dependências: `httpx` já presente, endpoint JSON público responde a cliente padrão (sem necessidade de `curl_cffi`).
