# Tasks

## 1. Implementação
- [x] 1.1 Config: `SERVICE_MONITOR_LOG_FORMAT` (`json` | `pretty`) e `SERVICE_MONITOR_STATUS_BOARD_MINUTES`, com aviso para valor recusado.
- [x] 1.2 Nível da rotina no scheduler: `DEBUG` quando status e motivo não mudaram.
- [x] 1.3 Retrato com último erro e evento de mudança no `NotificationManager`; `app/core/board.py` com montagem e cadência do quadro.
- [x] 1.4 `PrettyFormatter`, com linha curta por evento e tabela para `status_board`.

## 2. Documentação
- [x] 2.1 `.env.example`, `docker-compose*.yml`, `deployment.example.yaml`, `DOCKER.md` e `README.md`.

## 3. Testes
- [x] 3.1 Testes do nível da rotina, da cadência do quadro (subida, mudança agrupada, intervalo, `0`), do formatter `pretty` (alinhamento, truncamento, allowlist, credencial) e do evento JSON.
- [x] 3.2 Execução real do app em `pretty`, com a saída registrada no PR.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate add-log-status-board --strict`.
- [x] 4.3 Em produção, `kubectl logs --tail=30` mostra um quadro recente, e a rotina `OK` não aparece em `INFO`. Verificado em 2026-11-19 (didevlab/housek8s#524): `status_board` com 12 OK e só a primeira rodada em `INFO` dois minutos após a subida.
