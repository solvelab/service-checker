## Context
O `NotificationManager` despacha um evento por chamada (`send_alert` por componente,
`send_recovery`, `send_monitor_error`, `send_monitor_recovered`) e só avança o próprio estado quando
algum canal devolve `True`. Os módulos rodam em tarefas asyncio concorrentes, e cada um chama
`handle_result` no fim do próprio ciclo. O contrato de notificador (quatro métodos, retorno `bool`)
é checado no registro e por teste de AST.

## Goals / Non-Goals
- Goals: uma mensagem por chat por dia; notificação só para problema novo; sobreviver a restart;
  nunca transformar uma falha da Bot API em silêncio; rollback por configuração.
- Non-Goals: mudar outros canais; mudar o state machine do manager; comandos de bot; resumo enviado
  em horário fixo sem evento.

## Decisions
- **O modelo do dia mora no notificador do Telegram.** `DailySummary` (em
  `app/notifications/telegram/summary.py`) guarda o dia, as `SummaryEntry` (uma por problema, chave
  estável) e o `message_id` de cada chat. Cada chamada do manager vira uma atualização idempotente do
  modelo seguida de uma escrita do resumo inteiro. Idempotente porque o manager reenvia o mesmo
  evento quando o canal devolve `False`.
- **Chave do problema.** Componente: `module_id:<id|slug|name>` do item, a mesma identidade de
  `_service_key`. Alerta de módulo: `module_id`. Falha de monitoramento: `module_id:monitoring`. A
  recuperação chega com o mesmo item e resolve a mesma chave.
- **Novo vs. repetição.** Um `send_alert`/`send_monitor_error` para uma chave sem entrada aberta é
  problema novo; com entrada aberta é repetição (atualiza status e `last_seen_at`).
- **Coalescência por janela.** O reenvio com notificação tem uma janela de 60s (`_RESEND_COOLDOWN_SECONDS`): um problema novo dentro dela edita
  em vez de reenviar, porque o toque acabou de acontecer. Isso evita N toques num incidente com N
  componentes sem mudar o contrato nem o manager. Um `asyncio.Lock` serializa modelo e escrita entre
  módulos concorrentes. `# lean: janela fixa -> configurável se 60s se mostrar curto ou longo`.
- **Toque devido.** Um problema que ainda não tocou em chat nenhum (`notified` falso) é uma dívida
  que a próxima escrita paga — o reenvio do manager, o evento de outro módulo ou o flush —, e por
  chat, um reenvio com toque que falhou fica em `_ring_owed`. Uma edição silenciosa de outro problema
  nunca marca como notificado o que não tocou (achado do bug-hunter).
- **Flush depois de cada ciclo.** O manager chama `flush()` do Telegram ao fim de `handle_result`. Ele
  reescreve só o chat cujo último texto escrito difere do resumo atual, então num dia quieto não faz
  chamada nenhuma. Existe porque o manager descarta o estado de uma recuperação de serviço seja qual
  for a resposta: uma edição recusada com 429 deixaria o problema aberto na tela o resto do dia.
- **Escrita por chat.**
  - Problema novo fora da janela: `deleteMessage` da mensagem do dia (falha só registrada) +
    `sendMessage` com notificação; guarda o novo `message_id`.
  - Demais: `editMessageText`. `message is not modified` conta como entregue. Mensagem inexistente
    ou não editável: `sendMessage` silencioso (`disable_notification`) e guarda o id.
  - Sem `message_id` para o dia (primeira mensagem, virada, restart sem estado): `sendMessage`, com
    notificação só se o evento for problema novo.
  - Retorno `True` se algum chat gravou o resumo.
- **Virada do dia** no fuso do resumo, só para a frente (um evento com carimbo de antes da meia-noite,
  atrasado pelo lock, não volta o dia): entradas resolvidas ficam no dia anterior; abertas com
  `last_seen_at` dentro de `NOTIFICATION_STATE_MAX_AGE_MINUTES` (24h quando ele é 0, "sem limite")
  são carregadas; as demais saem,
  porque nenhum evento as confirmou (o manager pode ter descartado o estado delas).
- **Persistência.** `StateStore.save/load` ganham a chave `telegram_summary` (opaca para o store, um
  dict), lida e escrita pelo manager via `export_state()`/`restore_state()` do notificador, quando
  ele os tiver. Sem subir `SCHEMA_VERSION`: um arquivo antigo não tem a chave e um binário antigo
  ignora a chave, então upgrade e rollback preservam os alertas pendentes.
- **Chave dos componentes sem id.** A recuperação chega com `from_status`/`to_status`; eles saem antes
  de calcular a chave, para o digest do conteúdo bater com o do alerta. A chave da falha de
  monitoramento é `monitor-error/<módulo>`, sem ":", e não colide com chave de componente.
- **Limite de 4096**, contado em unidades UTF-16 com a marcação (cada 🔴/✅ vale 2). O render tenta completo; se não couber, cada serviço mostra só os
  últimos 3 resolvidos mais a contagem; se ainda não couber, só contagens dos resolvidos; os abertos
  sempre aparecem; no último recurso o corte é por linha, com um rodapé que conta as linhas omitidas.
- **Defaults.** O loader de env usa `daily_summary`; o dataclass `TelegramConfig` usa `per_event`,
  para que as construções posicionais existentes (testes do contrato) continuem descrevendo o modo
  atual.

## Risks / Trade-offs
- Flapping de monitoramento: cada falha que atinge o limiar é problema novo e notifica (37 em 14h no
  replay da #87, numa só mensagem). Mitigação futura em item próprio.
- Limite de taxa da Bot API por chat: no máximo uma escrita por chat por evento, com o lock
  serializando.
- Perda do estado: nasce uma mensagem nova no meio do dia; degradação visível, sem perder alerta.

- Timeout do nosso lado num `sendMessage` que o Telegram entregou: o manager tenta de novo, sai um
  segundo toque e a primeira mensagem fica órfã. A Bot API não tem chave de idempotência; fica como
  lacuna conhecida.

## Migration Plan
Deploy com o default novo; rollback por `TELEGRAM_MODE=per_event` no manifesto, sem nova imagem. O
`state.json` existente é lido sem mudança.
