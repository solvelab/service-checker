# Tasks

## 1. Implementação
- [x] 1.1 `app/modules/retell/__init__.py` e `monitor.py`, com `RetellStatusMonitor` e `get_monitor()`, no molde do `bitbucket`.
- [x] 1.2 A regra `status` usa os estados degradados default quando o valor configurado não nomeia nenhum estado de componente.
- [x] 1.3 URL default do slug `retell` em `app/core/config.py::_default_url`.

## 2. Documentação
- [x] 2.1 `app/modules/retell/README.md`, com a tabela dos componentes reais, e a navegação dos READMEs irmãos.
- [x] 2.2 `.env.example`, `DOCKER.md`, `deployment.example.yaml`, `README.md` e `docker-compose*.yml`.

## 3. Testes
- [x] 3.1 `tests/test_retell_monitor.py` com o `summary.json` real em `tests/fixtures/retell/`. Cobre: saudável, degradado, enriquecimento, falha do enriquecimento, rede, timeout, filtro, keyword, regex, componentes vazios, monitor não configurado e o default `major,minor`.
- [x] 3.2 Checagem ao vivo pelo módulo contra `status.retellai.com`: `SERVICE_MONITOR_MODULES=retell python -m app`, sem nenhuma `RETELL_*`, em 2026-11-18T18:31:57Z deu `retell status healthy`, `status=OK` e `duration_ms=523`.
- [x] 3.3 bug-hunter no diff: 10 ataques. Corrigidos com teste no `retell`: um corpo de enriquecimento com formato errado derrubava o `check()` e o alerta real junto, e um campo nulo saía como `None` no motivo. Herdados dos irmãos e levados a outra tarefa: o mesmo defeito do enriquecimento no `bitbucket`, no `github` e no `cloudflare`, e o texto do enriquecimento que não chega aos canais. Aceitos e documentados: um filho degradado alerta junto com o grupo, e a regra `operational` alerta em tudo, como pedido.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate add-retell-monitor --strict`.
- [x] 4.3 Em produção, o startup lista `retell` e a checagem dá `OK` ou `ALERT`. O pod da `v2.12.0` iniciou às 2026-11-18T18:45:41Z com 11 módulos e `module_load` do `retell`, e a primeira checagem deu `OK` em 405 ms (didevlab/housek8s#518).
