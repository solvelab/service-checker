# Change: Startup announcement and daily heartbeat

## Why
Issue #124. O checker só fala quando algo quebra: os canais recebem alerta, recuperação, falha e recuperação do monitoramento, e nada mais. O evento `startup` vai só para o log. No Telegram em `daily_summary`, o reenvio do resumo na subida é efeito colateral do resumo, e numa instalação só com Google Chat ele não existe.

Silêncio no canal tem então duas leituras: "tudo saudável" ou "o checker não roda, ou não entrega". Quem sobe o checker num cluster novo não sabe se o pod subiu, se o webhook entrega, nem se cada provedor está sendo checado. Um `SERVICE_FILTER` errado, por exemplo, deixa o módulo em `ERROR` em silêncio até o limiar. Depois da subida, um pod que morre e não volta deixa o canal tão quieto quanto um dia saudável.

## What Changes
- **Aviso de subida.** Quando todo módulo carregado entregar o primeiro resultado, ou quando vencer `NOTIFICATION_STARTUP_TIMEOUT_SECONDS` (default 120), os canais humanos recebem uma mensagem com:
  - a versão;
  - o pod (`HOSTNAME`);
  - os canais ligados;
  - o primeiro resultado de cada provedor, com o motivo quando houver.

  Um módulo sem resultado no prazo aparece como "sem resultado". O aviso é ligado por default e pode ser desligado com `NOTIFICATION_STARTUP_ANNOUNCE`.
- **Heartbeat diário.** Uma mensagem por dia, em `NOTIFICATION_HEARTBEAT_TIME` (`HH:MM`, default `09:00`; vazio desliga) no fuso `NOTIFICATION_HEARTBEAT_TIMEZONE` (default `America/Sao_Paulo`). Ela traz o tempo no ar e o último resultado de cada provedor. O horário vale a partir da próxima ocorrência depois da subida, sem compensar o dia perdido: o aviso de subida já cobre esse dia. Assim um restart nunca repete o heartbeat do dia.
- **Canais humanos.** Telegram, webhook e Google Chat implementam `send_status_report`. O Alertmanager não implementa, porque é registro e não entrega a humano. No Telegram, o relatório é uma mensagem nova nos dois modos: uma edição do resumo diário não notifica o telefone, e o heartbeat só vale se notificar.
- **Entrega.** Cada canal reporta se entregou, pelo contrato existente. Um relatório que nenhum canal aceitou é tentado de novo até 3 vezes, a cada 60s. A falha de um canal é logada com o motivo e não afeta os outros nem o monitoramento.

## Impact
- Affected specs: `notifications`.
- Affected code:
  - `app/core/types.py`: protocolo `StatusReporter`;
  - `app/core/notifications.py`: retrato por módulo e despacho do relatório;
  - `app/core/reports.py` (novo): agenda do aviso e do heartbeat;
  - `app/core/scheduler.py` e `app/main.py`: ligação;
  - `app/core/config.py`: variáveis novas;
  - `app/notifications/{telegram,webhook,google_chat}`: formatação;
  - docs de configuração e `scripts/simulate_notifications.py`.
