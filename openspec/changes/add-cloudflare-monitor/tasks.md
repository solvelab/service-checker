# Tasks

## 1. Implementation
- [x] 1.1 Criar `app/modules/cloudflare/__init__.py` e `monitor.py` com `CloudflareStatusMonitor` e factory `get_monitor()`.
- [x] 1.2 Implementar regras `status`, `keyword` e `regex` espelhando o módulo `bitbucket`.
- [x] 1.3 Implementar `_watchlist()`: allowlist curada quando o filtro está vazio, `*` como escape hatch, entradas em branco descartadas antes de decidir se algo foi configurado.
- [x] 1.4 Implementar `_warn_about_missing()`: log WARNING nomeando os vigiados ausentes do payload.
- [x] 1.5 Implementar `_enrich_reason` consultando `incidents/unresolved.json` e `scheduled-maintenances/active.json`, com fallback silencioso e `reason` inalterado quando nada enriquece.
- [x] 1.6 Registrar URL default em `app/core/config.py::_default_url` para o slug `cloudflare`.

## 2. Wiring
- [x] 2.1 Adicionar `cloudflare` a `SERVICE_MONITOR_MODULES` no `.env.example`, nos dois compose e no `deployment.yaml`. O módulo `bitbucket` entrou em e13cc61 e só foi habilitado no k8s depois, em 610f923 — esta é a lacuna a não repetir.
- [x] 2.2 Adicionar as oito `CLOUDFLARE_*` nos mesmos quatro arquivos.
- [x] 2.3 Adicionar entrada e degradação própria em `scripts/simulate_alerts.py`: `_cloudflare_degrade` derruba `Tunnel`, porque `_statuspage_degrade` quebraria `components[0]`, que aqui está fora da allowlist.
- [x] 2.4 Declarar o contrato de campos em `scripts/simulate_endpoints.py::CONTRACTS`; sem isso o
  módulo aparecia como `no contract (HTML)` e seus campos nunca eram verificados ao vivo.

## 3. Documentation
- [x] 3.1 Criar `app/modules/cloudflare/README.md`, com a seção explicando por que filtro vazio não é "tudo" aqui.
- [x] 3.2 Atualizar a barra de navegação `🔗 Nav:` nos nove READMEs irmãos.
- [x] 3.3 Atualizar `README.md` (nav, lista de provedores, rodapé) e `DOCKER.md` (bloco `CLOUDFLARE_`).

## 4. Validation
- [x] 4.1 Capturar payload real em `tests/fixtures/cloudflare/summary.json` — 475 componentes, 51 fora de `operational`.
- [x] 4.2 Criar `tests/test_cloudflare_monitor.py` — 53 testes. ✓
- [x] 4.3 Teste decisivo: a fixture real, com configuração default, produz `OK`. ✓
- [x] 4.4 Teste de controle: os 51 degradados da fixture são todos PoP, nenhum produto — senão 4.3 passaria pelo motivo errado. ✓
- [x] 4.5 Bug-Hunter: `components` como dicionário; componente sem `name`, sem `status`, sem `id`; status fora do enum; filtro só com brancos; `*` junto de outras entradas; dois componentes com o mesmo nome; payload com 1000 níveis de aninhamento. ✓ **Dois defeitos encontrados e corrigidos**: filtro só-brancos caía em watchlist vazia em vez do default, e `_extract_components` levantava `AttributeError` com `components` malformado.
- [x] 4.6 `ruff check app tests scripts`. ✓
- [x] 4.7 `pytest tests/ -v` sem regressão. ✓
- [x] 4.8 `python scripts/simulate_endpoints.py .env.example` — dez módulos alcançáveis. ✓
- [x] 4.9 `python scripts/simulate_alerts.py` — dez provedores alertam e recuperam, exit 0. ✓
- [x] 4.10 `openspec validate add-cloudflare-monitor --strict`. ✓

## 5. Rollout
- [x] 5.1 `cloudflare` presente em `SERVICE_MONITOR_MODULES` no `deployment.yaml`.
- [ ] 5.2 Confirmar nos logs `module_id=cloudflare` com `status=OK` e `duration_ms` numérico após o deploy.
  — pendente de verificação pelo operador (requer acesso ao cluster).
