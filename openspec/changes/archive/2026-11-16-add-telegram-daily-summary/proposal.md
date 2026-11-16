# Change: Telegram daily summary message

## Why
Issue #100. O Telegram recebe uma mensagem nova a cada evento: `TelegramNotifier` só chama
`sendMessage`, sem ler o `message_id` da resposta, e um alerta aberto é reenviado a cada
`NOTIFICATION_REPEAT_MINUTES` (10 no deployment). No cluster, em 2026-11-16, o `claude` com
`degraded_performance` mandou cartões às 03:34, 03:44 e 03:54 UTC — cerca de 144 por dia para um
problema que não mudou. Com o `steam` oscilando foram 101 mensagens em 14h (#87). O volume gera
fadiga, o grupo passa a ser ignorado, e o chat não responde o que importa: o que aconteceu hoje, o que
segue aberto e quando cada coisa foi resolvida. Os horários também saem em UTC, porque o pod roda em
UTC com `TELEGRAM_TIMESTAMP_ZONE=LOCAL`.

## What Changes
- Novo modo do Telegram, `TELEGRAM_MODE=daily_summary` (default do loader de env): uma mensagem por
  chat por dia, no fuso `TELEGRAM_SUMMARY_TIMEZONE` (IANA, default `America/Sao_Paulo`).
- A mensagem lista, por serviço, a contagem do dia, os problemas abertos com a hora de início e os
  resolvidos com início, fim e duração; as falhas de monitoramento ficam numa seção própria, com o
  motivo.
- Só um problema **novo** notifica: o bot apaga a mensagem do dia e a reenvia atualizada no fim do
  chat. Repetição, recuperação e restabelecimento editam em silêncio (`editMessageText`).
- Na virada do dia, o primeiro evento cria a mensagem do novo dia, sem notificação a menos que o
  próprio evento seja um problema novo, carregando os problemas ainda abertos. A anterior fica como
  registro.
- O `message_id` de cada chat e as entradas do dia vão para o `state.json`, numa chave nova
  (`telegram_summary`), sem subir `SCHEMA_VERSION`.
- Falhas da Bot API nunca viram silêncio: `message is not modified` conta como entregue; mensagem
  inexistente ou não editável leva a uma mensagem nova; falha do `deleteMessage` não impede o envio.
- `TELEGRAM_MODE=per_event` mantém o comportamento atual, como rollback sem código.
- Não muda: Alertmanager, Google Chat, webhook e o state machine do `NotificationManager`.

## Impact
- Affected specs: `notifications`
- Affected code: `app/notifications/telegram/notifier.py`, `app/notifications/telegram/summary.py`
  (novo), `app/notifications/telegram/templates/telegram_daily_summary.j2` (novo),
  `app/core/config.py`, `app/core/state_store.py`, `app/core/notifications.py` (persistir o estado do
  canal), docs de configuração e `scripts/simulate_notifications.py`.
