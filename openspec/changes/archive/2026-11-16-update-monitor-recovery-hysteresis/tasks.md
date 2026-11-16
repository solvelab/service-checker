# Tasks

## 1. Configuração
- [x] 1.1 `NOTIFICATION_RECOVERY_THRESHOLD` em `app/core/config.py` (default 3, piso 1), campo
  `recovery_threshold` em `NotificationConfig`.

## 2. Estado
- [x] 2.1 `MonitorErrorState.consecutive_successes`.
- [x] 2.2 `StateStore` grava e lê o campo; estado antigo sem ele carrega como 0.

## 3. NotificationManager
- [x] 3.1 `_clear_monitor_error`: falha notificada só encerra depois de M sucessos seguidos.
- [x] 3.2 `_handle_monitor_error`: `ERROR` zera os sucessos; `last_notified_at` preservado faz o
  throttle agir.
- [x] 3.3 `monitor_blind_window` só quando a sequência atingiu o limiar.

## 4. Testes
- [x] 4.1 `E E E O E E E O O O` com 3/3: 1 falha, 1 restabelecimento.
- [x] 4.2 `ERROR` na janela não reenvia antes do throttle; depois do throttle, reenvia.
- [x] 4.3 Limiar 1 mantém o comportamento anterior.
- [x] 4.4 Recuperação de serviço não espera a histerese.
- [x] 4.5 Persistência: janela sobrevive a restart; estado antigo carrega.
- [x] 4.6 `E E O` com limiar 3 não loga blind window; envio recusado loga.
- [x] 4.7 Replay da sequência real do `steam` (814 resultados): contagens da tabela do proposal (51/47 com M=1, 37/15 com M=3).

## 4b. Achados do bug-hunter
- [x] 4b.1 Restabelecimento recusado não remove o estado; o próximo sucesso tenta de novo.
- [x] 4b.2 Erro na janela reinicia a contagem de erros consecutivos (texto verdadeiro).
- [x] 4b.3 Entradas de erro persistidas com data futura ou além de max_age são descartadas.
- [x] 4b.4 `consecutive_successes` booleano no arquivo normaliza para 0.

## 5. Documentação
- [x] 5.1 `DOCKER.md`, `.env.example`, `deployment.example.yaml`, `docker-compose*.yml`,
  `app/notifications/README.md`, `README.md`.
- [x] 5.2 Docstring de `app/core/impersonated_fetch.py` com a medição de 2026-11-15.

## 6. Validação
- [x] 6.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 6.2 `python scripts/simulate_notifications.py` verde.
- [x] 6.3 `openspec validate update-monitor-recovery-hysteresis --strict`.
