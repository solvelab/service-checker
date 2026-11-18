# Tasks

## 1. Implementação
- [x] 1.1 `app/modules/elevenlabs/__init__.py` e `monitor.py`, com `ElevenLabsStatusMonitor` e `get_monitor()`.
- [x] 1.2 Regra `status` pela severidade mínima do indicador; itens por incidente aberto, item da página quando não há incidente, componentes degradados com filtro.
- [x] 1.3 URL default do slug `elevenlabs` em `app/core/config.py::_default_url`.

## 2. Documentação
- [x] 2.1 `app/modules/elevenlabs/README.md` (fonte incident.io e regra), navegação dos READMEs irmãos, contagem de provedores.
- [x] 2.2 `.env.example`, `DOCKER.md`, `deployment.example.yaml`, `README.md` e `docker-compose*.yml`.

## 3. Testes
- [x] 3.1 `tests/test_elevenlabs_monitor.py` com o `summary.json` real (incidente aberto) e a variante saudável.
- [x] 3.2 `scripts/simulate_endpoints.py` e `scripts/simulate_alerts.py` com o `elevenlabs`; checagem ao vivo pelo app. Em 2026-11-18T19:12:20Z, `SERVICE_MONITOR_MODULES=elevenlabs python -m app`, sem nenhuma `ELEVENLABS_*`, deu `ALERT` pelo incidente real (`Partial System Degradation: Increased latency and API errors: identified`, 497 ms). As duas simulações passaram: `all 12 providers alerted on degradation and recovered afterwards` e `all 12 modules reachable`.
- [x] 3.3 bug-hunter no diff, de ponta a ponta pelo `NotificationManager`: 11 ataques. A primeira versão trocava o item da página pelos itens de incidente, e o indicador decidia se um incidente aberto existia. Uma queda contínua virava alerta, recuperação e alerta de novo, um incidente aberto era anunciado como restaurado quando o indicador baixava, e um indicador vazio ou desconhecido virava `OK`. Foram corrigidos com teste: o item `page` agora convive com os incidentes, o incidente é filtrado pelo próprio impacto, um indicador desconhecido é `ERROR`, os ids têm prefixo (`page`, `incident:`, `component:`), o estado do incidente passa por `strip`, e `none` nunca alerta. Ficam como lacuna, por serem do manager: a escalada de severidade numa mesma chave só é avisada na janela de repetição; ids que diferem só em maiúsculas colidem; não há teto para muitos incidentes num ciclo.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate add-elevenlabs-monitor --strict`.
- [ ] 4.3 Em produção, o startup lista `elevenlabs` e a checagem dá `OK` ou `ALERT`.
