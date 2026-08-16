# Change: Add monitoring-failure notifications

## Why
`MonitorStatus.ERROR` não gera notificação alguma. Só `ALERT` chama `_notify_alert` e só a
transição `ALERT → OK` chama `_notify_recovery` (`app/core/notifications.py`). Um monitor que
perdeu acesso ao upstream fica **permanentemente mudo**: nenhum card no Telegram, nenhum POST no
webhook, apenas uma linha de log a cada ciclo que ninguém lê sem agregador.

Não é teórico. O módulo `steam` esteve exatamente nesse estado em produção — HTTP 403 do
Cloudflare por TLS fingerprint, corrigido na v2.2.2 — e o sistema de notificação ficou satisfeito o
tempo todo. Medição do comportamento atual: 60 ciclos consecutivos de `ERROR` produzem **zero**
notificações.

O agravante já foi parcialmente mitigado na v2.2.3, que impediu o `ERROR` de engolir a recovery
pendente. Mas o ponto cego principal continua: ninguém é avisado de que o monitoramento parou.

## What Changes
- Notificar após N ciclos consecutivos de `ERROR` no mesmo módulo, com N configurável via
  `NOTIFICATION_ERROR_THRESHOLD` (default `3`).
- Notificar a saída do estado de erro, quando o módulo volta a conseguir avaliar o upstream.
- Distinguir claramente "não consegui checar" de "o serviço está fora" — são eventos diferentes
  para quem está de plantão.
- Respeitar `NOTIFICATION_REPEAT_MINUTES` no erro sustentado, como já acontece com alerta.
- Novo template Telegram `telegram_monitor_error.j2` e novo `telegram_monitor_recovered.j2`.
- Novos valores de `status` no payload do webhook: `MONITOR_ERROR` e `MONITOR_RECOVERED`,
  distintos dos `ALERT` e `RESOLVED` existentes.

## Impact
- Affected specs: `notifications` (ADDED requirements).
- Affected code:
  - `app/core/notifications.py` — contador de erros consecutivos no `AlertState`, novo caminho de
    notificação, novos métodos `_notify_monitor_error` / `_notify_monitor_recovered`
  - `app/core/config.py` — `NOTIFICATION_ERROR_THRESHOLD` em `NotificationConfig`
  - `app/notifications/telegram/notifier.py` — `send_monitor_error` / `send_monitor_recovered`
  - `app/notifications/telegram/templates/` — dois templates novos
  - `app/notifications/webhook/notifier.py` — os dois métodos equivalentes
  - `.env.example`, `docker-compose.yml`, `docker-compose-dev.yml`, `deployment.yaml`, `DOCKER.md`
  - `app/notifications/README.md` — documentar o novo ciclo de vida
  - `tests/test_monitor_error_notifications.py` — nova suíte
- **Breaking para consumidores de webhook que assumem um conjunto fechado de `status`.** Um
  consumidor que hoje filtra por `ALERT`/`RESOLVED` continua funcionando; um que faça `else: raise`
  em valor desconhecido precisa ser atualizado. Registrar no CHANGELOG.
- Não-breaking para Telegram: os cards existentes não mudam.
- Sem dependências novas.
- Escopo por módulo, não por serviço: todo `ERROR` produz `payload=None` (falha de rede) ou `dict`
  (filtro sem match), nunca `list[dict]`, então sempre cai no branch por módulo. Medido nos sete
  monitores.
