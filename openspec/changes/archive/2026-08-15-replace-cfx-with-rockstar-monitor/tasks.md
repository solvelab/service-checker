## 1. Investigation
- [x] 1.1 Identificar a fonte de dados em `https://support.rockstargames.com/servicestatus`. **Resultado**: não há endpoint JSON; dados vêm em HTML server-rendered com `data-testid` estáveis (`status-hero-heading`, `status-hero-metadata`, `status-section-heading`, `status-item-N`).
- [x] 1.2 Confirmar requisitos de transporte. **Resultado**: WAF bloqueia `httpx`/`requests` por TLS fingerprint. `curl_cffi` com `impersonate="chrome110"` ou `"safari17_0"` retorna 200 do pod do cluster. Fixture salva em `tests/fixtures/rockstar/all_operational.html`.
- [x] 1.3 Mapear serviços. **Resultado**: 16 items em 4 seções — Grand Theft Auto Online (PS5/PS4/XSX/XB1/PC), Red Dead Online (PS4/XB1/PC), Online Services (Authentication/Launcher/Store), Cfx.re (Authentication/FiveM/RedM/Community Servers/Marketplace).

## 2. Design
- [x] 2.1 Matriz de severidade definida (ver `design.md`): hero "operational" → OK; hero "outage"/"down"/qualquer item filtrado não-operational → ALERT; falha de rede/parse → ERROR.
- [x] 2.2 Payload: lista de dicts `{id, name, section, status, status_text}` por item. Sem `updated_at` por item (não exposto upstream); apenas `updated_at` global do hero no campo top-level do payload.
- [x] 2.3 Defaults: `enabled=True`, `interval_seconds=60`, `timeout_seconds=15`, `service_filter=[]`, `IMPERSONATE_PROFILE=chrome110`.

## 3. Implementation
- [x] 3.1 Criar `app/modules/rockstar/` com `__init__.py`, `monitor.py` (classe `RockstarStatusMonitor`) e `README.md`.
- [x] 3.2 Implementar cliente HTTP via `curl_cffi` (TLS impersonation) com timeout, parsing defensivo e fallback `ERROR` em falhas de rede/parse. `_fetch_html` roda em `asyncio.to_thread` para não bloquear o loop.
- [x] 3.3 Implementar avaliação de regra por `service_filter` (id, name ou section, case-insensitive) + classificação por substring no texto de status.
- [x] 3.4 Registrar slug `rockstar` em `app/core/config.py` (URL default) e remover registro `cfx`.
- [x] 3.5 Remover `app/modules/cfx/` e atualizar todos os Nav links nos READMEs dos demais módulos.
- [x] 3.6 Atualizar `deployment.yaml`, `docker-compose.yml`, `docker-compose-dev.yml` substituindo bloco `CFX_*` por `ROCKSTAR_*` e a lista `SERVICE_MONITOR_MODULES`.
- [x] 3.7 Adicionar nota de migração em `app/modules/rockstar/README.md` (config key `cfx` → `rockstar`); atualizar `README.md` raiz e `DOCKER.md`.
- [x] 3.8 Adicionar `curl_cffi==0.7.4` em `requirements.txt`.

## 4. Validation
- [x] 4.1 Teste unitário com fixture real (`all_operational.html`) → cenário healthy retorna `OK` com 16 services. ✓ pytest passou.
- [x] 4.2 Teste com fixture editada (FiveM marcado degradado) → retorna `ALERT` com payload contendo apenas FiveM. ✓ pytest passou.
- [x] 4.3 Teste de resiliência: rede falha (`_fetch_html` lança), HTML sem hero, filtro sem match → todos retornam `ERROR` com `duration_ms` preenchido. ✓ pytest passou (12/12 testes Rockstar; 43/43 suite completa).
- [x] 4.4 Smoke test contra endpoint real do pod: `STATUS=OK`, `duration=119-322ms`, 16 services parseados, `updated_at` extraído ("As of April 26, 2026 @ 4:40 PM UTC"). Filtro `FiveM,RedM` retorna 2 services. ✓
- [x] 4.5 Isolamento: `_fetch_html` usa `curl_cffi.requests.get` num thread separado via `asyncio.to_thread` — exceções viram `MonitorResult(ERROR)` e nunca propagam para o scheduler. Suite de testes existente (github, recovery_notifications) continua passando: 31 outros testes ✓.

## 5. Rollout
- [x] 5.1 Rebuild da imagem Docker incluindo `curl_cffi` (já em `requirements.txt`). ✓ `curl_cffi==0.7.4` em `requirements.txt`; imagens `v2.1.0` e `v2.2.0` publicadas em `ghcr.io/solvelab/service-checker`.
- [ ] 5.2 Atualizar deployment do cluster (`monitoring/de-service-checker`) — `deployment.yaml` já atualizado neste branch; aplicar via `kubectl apply -f deployment.yaml`.
  — pending operator verification (requires cluster access).
- [ ] 5.3 Confirmar nos logs ausência de `module_id=cfx` e presença de `module_id=rockstar` com `status=OK`.
  — pending operator verification (requires cluster access). Simulação local em 2026-08-15 contra
  o endpoint real retornou `rockstar status=OK, duration_ms=155`, payload
  `dict keys=['hero', 'services', 'updated_at']`.
