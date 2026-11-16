# Tasks

## 1. Config e estado
- [x] 1.1 `TelegramConfig.mode` (`per_event` no dataclass) e `summary_timezone`; loader com `TELEGRAM_MODE` (default `daily_summary`) e `TELEGRAM_SUMMARY_TIMEZONE` (default `America/Sao_Paulo`), valor inválido registrado e trocado pelo default.
- [x] 1.2 `StateStore` persiste e restaura `telegram_summary` sem subir `SCHEMA_VERSION`; manager liga `export_state`/`restore_state`.

## 2. Resumo
- [x] 2.1 `DailySummary` / `SummaryEntry`: chave do problema, novo vs. repetição, resolução, virada do dia com carga dos abertos válidos.
- [x] 2.2 Template `telegram_daily_summary.j2` e render com o limite de 4096.
- [x] 2.3 `TelegramNotifier` no modo resumo: delete + send com notificação, edit silencioso, janela de reenvio, lock, tratamento de erros da Bot API.

## 3. Testes
- [x] 3.1 Transições: novo, repetição, recuperação, falha de monitoramento, virada, restart.
- [x] 3.2 Erros da Bot API: not modified, mensagem inexistente, delete falhando, todos os chats falhando.
- [x] 3.3 Render: fuso, seção de monitoramento, limite de 4096.
- [x] 3.4 Replay do dia do `steam` + alerta de 2h pelo manager real, contando send/edit/delete.
- [x] 3.5 `per_event` mantém a suíte atual.
- [x] 3.6 bug-hunter no diff: 11 achados corrigidos com teste (chave por digest, restauração malformada, toque perdido, dia voltando, recuperação recusada, divergência por chat, horário corrompido, carga sem limite, UTF-16, colisão de chave, corte que escondia problema novo); timeout que entregou fica como lacuna.

## 4. Documentação e simulação
- [x] 4.1 README do Telegram, `.env.example`, `DOCKER.md`, `deployment.example.yaml`.
- [x] 4.2 `scripts/simulate_notifications.py` com o modo resumo.

## 5. Validação
- [x] 5.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 5.2 `openspec validate add-telegram-daily-summary --strict`.
- [x] 5.3 Em produção, print do grupo com a mensagem do dia (enviado pelo usuário em 2026-11-16, 08:59 BRT: uma mensagem, 6 abertos, 24 no dia).
