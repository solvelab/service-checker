# Change: Status board in the log, routine checks below INFO

## Why
Issue #125. O log é JSON, uma linha por evento, e cada checagem gera um `monitor_check` em `INFO`, saudável ou não. No pod de produção, em 10 minutos de 2026-11-19 saíram 121 linhas, 119 delas `monitor_check` `OK`. Um `WARNING` some no meio, não há visão do estado atual, e o JSON em linha é ruim de ler no terminal. Sem dashboard, `kubectl logs` é o único painel.

## What Changes
- **Rotina abaixo de `INFO`.** Um `monitor_check` cujo status e motivo não mudaram desde a checagem anterior do mesmo módulo sai em `DEBUG`. A primeira checagem de cada módulo e toda mudança de status ou de motivo saem no nível de sempre: `INFO` para `OK`, `WARNING` para `ALERT`, `ERROR` para `ERROR`. Os outros eventos (`fetch_retry`, `notify_*`, conectividade, configuração) não mudam.
- **Quadro de status.** O evento `status_board` traz:
  - por provedor: status, desde quando, a última checagem e uma nota com o motivo atual, ou o último erro com horário quando o provedor já está saudável;
  - no rodapé: os canais, os alertas abertos e quantos provedores estão falhando.

  Ele sai depois da primeira rodada, a cada mudança de status (as mudanças próximas são agrupadas em um quadro só) e a cada `SERVICE_MONITOR_STATUS_BOARD_MINUTES` (default `15`; `0` desliga a cadência, mas mantém os quadros de subida e de mudança).
- **Formato legível.** `SERVICE_MONITOR_LOG_FORMAT` aceita `json` (o default, o formato atual) ou `pretty`. Em `pretty`, cada evento vira uma linha curta (`horário nível módulo mensagem chave=valor`), e o quadro vira uma tabela alinhada em até 100 colunas. Os campos são os mesmos da allowlist do JSON. Um valor desconhecido avisa e usa `json`.

## Impact
- Affected specs: `logging` (nova).
- Affected code:
  - `app/core/logging.py`: `PrettyFormatter` e seleção do formato;
  - `app/core/scheduler.py`: nível da rotina;
  - `app/core/notifications.py`: último erro no retrato e evento de mudança;
  - `app/core/board.py` (novo): montagem e cadência do quadro;
  - `app/core/types.py`, `app/core/config.py` e `app/main.py`;
  - docs de configuração.
