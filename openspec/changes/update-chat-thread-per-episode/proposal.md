# Change: Google Chat thread per alert episode

## Why
Issue #150. Desde a #142 o alerta, as repetições e a recuperação respondem numa conversa do Google Chat, mas a chave da thread é `<módulo>:<componente>` (`openai:responses`), fixa por componente. Quando o componente volta a falhar dias depois, o alerta novo responde na thread antiga, abaixo do "Recovered" do episódio anterior: dois episódios misturados, a thread velha sobe no espaço, e o time precisa rolar para achar o que é de agora.

## What Changes
- A chave da thread passa a ser `<módulo>:<componente>:<início>`, com o início em segundos Unix vindo de `MonitorResult.started_at`, que o manager preenche no alerta, na repetição e na recuperação a partir do `opened_at` persistido (#142). O episódio inteiro fica numa thread, inclusive depois de um restart; um episódio novo abre outra.
- Sem `started_at` (falhas de monitoramento, cujo estado não guarda o início), a chave continua a de hoje.

## Impact
- Affected specs: `notifications`.
- Affected code: `app/notifications/google_chat/notifier.py`, testes, README do Google Chat.
- Transição: um alerta aberto no deploy passa para uma thread nova uma vez; sem alerta repetido.
