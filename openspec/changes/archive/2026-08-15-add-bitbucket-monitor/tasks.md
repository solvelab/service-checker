# Tasks

## 1. Implementation
- [x] 1.1 Criar `app/modules/bitbucket/__init__.py` e `monitor.py` com classe `BitbucketStatusMonitor` e factory `get_monitor()`.
- [x] 1.2 Implementar regras `status`, `keyword`, `regex` e `service_filter` espelhando o módulo `github`.
- [x] 1.3 Implementar `_enrich_reason` consultando `incidents/unresolved.json` e `scheduled-maintenances/active.json` com fallback silencioso em falha.
- [x] 1.4 Registrar URL default em `app/core/config.py::_default_url` para o slug `bitbucket`.

## 2. Documentation
- [x] 2.1 Criar `app/modules/bitbucket/README.md` no padrão dos demais módulos (badges, nav, env vars, regras, componentes conhecidos, exemplos).
- [x] 2.2 Atualizar barra de navegação `🔗 Nav:` nos READMEs irmãos: steam, openai, claude, github, rockstar, oci, gcp, aws.

## 3. Validation
- [x] 3.1 Criar `tests/test_bitbucket_monitor.py` cobrindo: healthy, degraded, enriquecimento (incidents + maintenances), enrichment failure non-fatal, falha de rede, timeout, service filter (match e no-match), keyword, regex (válido e inválido), empty components, monitor não configurado, default rule targets.
- [x] 3.2 `python3 -m pytest tests/test_bitbucket_monitor.py -v` — 21/21 verdes.
- [x] 3.3 Suite restante sem regressões (rockstar/recovery falham por motivos pré-existentes deste host: `asyncio.to_thread` em py3.8 e Jinja2 antigo — não relacionados a esta change).
- [x] 3.4 `openspec validate add-bitbucket-monitor --strict`. ✓ `openspec validate --all --strict` → `4 passed, 0 failed` (openspec CLI 1.6.0, 2026-08-15).
- [x] 3.5 Smoke test contra endpoint real:
  ```bash
  SERVICE_MONITOR_MODULES=bitbucket SERVICE_MONITOR_LOG_LEVEL=DEBUG python3 -m app
  ```
  Esperar `bitbucket status healthy` (ou alerta legítimo).
  ✓ Executado em 2026-08-15 contra `https://bitbucket.status.atlassian.com/api/v2/summary.json`
  (Python 3.11.15, deps pinadas de `requirements.txt`): `status=OK`, `duration_ms=170`,
  13 componentes parseados com chaves `['id', 'name', 'slug', 'status']`.

## 4. Rollout
- [x] 4.1 Operador adiciona `bitbucket` em `SERVICE_MONITOR_MODULES` no deployment (ConfigMap). ✓ `deployment.yaml:23` lista `bitbucket`; imagem `v2.2.0` publicada no GHCR.
- [ ] 4.2 Confirmar nos logs `module_id=bitbucket` com `status=OK` e `duration_ms` numérico.
  — pending operator verification (requires cluster access).
