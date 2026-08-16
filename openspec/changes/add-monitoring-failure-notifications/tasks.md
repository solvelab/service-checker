# Tasks

## 1. Config
- [x] 1.1 Adicionar `error_threshold` em `NotificationConfig` (`app/core/config.py`), lido de
      `NOTIFICATION_ERROR_THRESHOLD` com default `3` e piso `1`, no mesmo padrão de `repeat_minutes`.

## 2. Máquina de estado
- [x] 2.1 Adicionar `MonitorErrorState` (`consecutive_errors`, `last_notified_at`, `last_reason`)
      num mapa proprio `_error_state`, **nao** no `AlertState` — ver Decisions no `design.md`.
- [x] 2.2 No branch por módulo, o caminho de `ERROR` incrementa `consecutive_errors` sem tocar
      `last_status`, `last_alert_at` nem `last_status_text`.
- [x] 2.3 Notificar falha de monitoramento quando `consecutive_errors >= error_threshold`,
      respeitando `NOTIFICATION_REPEAT_MINUTES` via `last_error_notified_at`.
- [x] 2.4 Em `OK` ou `ALERT`, zerar `consecutive_errors` e, se `last_error_notified_at` estiver
      preenchido, emitir monitoramento restabelecido e limpar o campo.
- [x] 2.5 Garantir que a ordem em `OK` seja: monitoramento restabelecido primeiro, recuperação de
      serviço depois.

## 3. Canais
- [x] 3.1 `telegram_monitor_error.j2` — ícone 🛑, título "Monitoring failure", com módulo, ciclos
      consecutivos e motivo do último erro.
- [x] 3.2 `telegram_monitor_recovered.j2` — ícone 🔄, título "Monitoring restored".
- [x] 3.3 `TelegramNotifier.send_monitor_error` / `send_monitor_recovered`, reaproveitando
      `_build_payload` e o fallback de render existente.
- [x] 3.4 `WebhookNotifier.send_monitor_error` / `send_monitor_recovered` com `status`
      `MONITOR_ERROR` / `MONITOR_RECOVERED`.

## 4. Config files e docs
- [x] 4.1 `NOTIFICATION_ERROR_THRESHOLD` em `.env.example`, `docker-compose.yml`,
      `docker-compose-dev.yml`, `deployment.yaml` e `DOCKER.md`.
- [x] 4.2 `app/notifications/README.md` — documentar o novo ciclo de vida e como silenciar
      (threshold alto).
- [x] 4.3 Nota no CHANGELOG sobre os novos valores de `status` do webhook.

## 5. Testes & Bug-Hunter
- [x] 5.1 Threshold: `N-1` erros não notificam; o N-ésimo notifica exatamente uma vez.
- [x] 5.2 Throttle: erro sustentado por 60 ciclos respeita `NOTIFICATION_REPEAT_MINUTES`.
- [x] 5.3 Recuperação: erro notificado seguido de `OK` emite monitoramento restabelecido.
- [x] 5.4 Recuperação silenciosa: erro abaixo do threshold seguido de `OK` não emite nada.
- [x] 5.5 Contador zera em `OK` e em `ALERT`; um segundo bloco de erros precisa reatingir o threshold.
- [x] 5.6 Composição com a v2.2.3: `ALERT` → erros acima do threshold → `OK` emite as **duas**
      notificações, e a de serviço preserva o `from_status` original.
- [x] 5.7 Threshold `0` ou negativo é elevado a `1`.
- [x] 5.8 Webhook emite `MONITOR_ERROR` / `MONITOR_RECOVERED`, nunca `ALERT` / `RESOLVED`.
- [x] 5.9 Renderização dos dois templates novos.
- [x] 5.10 Regressão: `tests/test_recovery_notifications.py` verde **sem alteração**.

## 6. Validação & Fechamento
- [x] 6.1 `openspec validate add-monitoring-failure-notifications --strict`.
- [x] 6.2 `ruff check app tests`.
- [x] 6.3 `pytest tests/ -v`.
- [x] 6.4 Simulação ao vivo dos 9 módulos sem regressão.
- [x] 6.5 Smoke: forçar `ERROR` num módulo apontando a URL para um host inválido e observar o card.
