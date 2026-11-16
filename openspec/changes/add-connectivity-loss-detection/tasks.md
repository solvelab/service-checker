# Tasks

## 1. Config e estado
- [x] 1.1 `NotificationConfig`: `connectivity_quorum`, `connectivity_tolerance_minutes`, `connectivity_windows`, `connectivity_timezone`; loader com `NOTIFICATION_CONNECTIVITY_QUORUM`, `NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES`, `NOTIFICATION_CONNECTIVITY_WINDOWS`, `NOTIFICATION_CONNECTIVITY_TIMEZONE`; valor inválido avisa e volta ao default.
- [x] 1.2 `MonitorErrorState.first_error_at` e `connectivity_loss`; `StateStore` persiste os campos e a seção `connectivity`.

## 2. Detecção
- [x] 2.1 `NotificationManager(module_ids=...)` e `main.py` passando os slugs carregados.
- [x] 2.2 Abertura por quórum, absorção dos módulos, fechamento abaixo do quórum com a contagem zerada.
- [x] 2.3 `_sync_connectivity`: tolerância, janelas, aviso como `checker`, recuperação e retentativa.
- [x] 2.4 Sem `monitor_blind_window` para módulo absorvido.

## 3. Testes
- [x] 3.1 Quórum atingido e não atingido; menos de 3 módulos.
- [x] 3.2 Tolerância abaixo e acima; `0`.
- [x] 3.3 Janela: dentro, saindo dela, cruzando a meia-noite, mal formada, ausente; falha isolada dentro da janela.
- [x] 3.4 Módulo que segue em erro depois da queda; restart no meio da queda; canal recusando.
- [x] 3.5 Replay da queda de 2026-11-16 07:00Z com os horários reais.
- [x] 3.6 Suíte atual (histerese, janela cega, persistência) sem mudança.
- [ ] 3.7 bug-hunter no diff.

## 4. Documentação e simulação
- [x] 4.1 `.env.example`, `DOCKER.md`, `deployment.example.yaml`, `app/notifications/README.md`.
- [x] 4.2 `scripts/simulate_notifications.py` com uma queda passando pelo manager e pelo resumo do Telegram.

## 5. Validação
- [ ] 5.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [ ] 5.2 `openspec validate add-connectivity-loss-detection --strict`.
- [ ] 5.3 Em produção, a próxima queda do roteador aparece como `connectivity_lost`/`connectivity_restored`, sem falhas por módulo.
