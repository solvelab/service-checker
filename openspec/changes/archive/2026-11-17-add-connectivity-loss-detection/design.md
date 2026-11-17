## Context
Os módulos rodam em tarefas asyncio concorrentes (`app/core/scheduler.py`), e cada um chama
`NotificationManager.handle_result` no fim do próprio ciclo. O estado de erro é por módulo
(`MonitorErrorState` em `_error_state`). O manager só conhece os módulos que já erraram; o total de
módulos carregados existe só em `app/main.py`.

## Goals / Non-Goals
- Goals: uma queda local vira no máximo um aviso; tolerância e janelas configuráveis por ambiente;
  falha isolada de provedor intacta; sobreviver a restart.
- Non-Goals: sonda para endpoint externo; silenciar erro de um provedor sozinho; mudar o contrato dos
  canais; mudar a detecção de `ALERT`.

## Decisions
- **Total de módulos.** `NotificationManager(config, module_ids=...)`; `main.py` passa os slugs dos
  monitores carregados. Sem `module_ids`, ou com menos de 3, a detecção fica desligada e o
  comportamento é o atual (os testes existentes constroem o manager assim).
- **"Em erro".** Um módulo está em erro quando tem `MonitorErrorState` com `consecutive_errors >= 1` e
  `consecutive_successes == 0`. Um módulo dentro da janela de histerese (sucessos contando) não conta.
- **Quando abre.** Em `_handle_monitor_error`, no ponto em que o módulo anunciaria a falha (limiar
  atingido): se `em_erro / total >= quórum`, abre a queda (`connectivity_lost` no log). O início é
  ancorado no `first_error_at` do módulo que completou o quórum, recuando só até `limiar × intervalo`
  antes dele, e ignora módulo com falha já anunciada sozinho: um provedor caído há horas não pode datar
  um reinício de 6 minutos em horas (achado do bug-hunter). Por isso `MonitorErrorState` ganha
  `first_error_at`. No replay de 07:00Z, quando o primeiro módulo atinge o limiar (07:02:19Z) os 10 já
  erraram (o último começou às 07:01:16Z).
- **Enquanto aberta.** Nenhum módulo anuncia, nem repetição de falha já anunciada. O módulo que
  atinge o limiar dentro da queda é marcado `connectivity_loss=True` e, ao voltar, sai sem
  `monitor_blind_window`, porque nada foi tentado para ele. O que atingiu o limiar antes do quórum
  teve o envio tentado e mantém o log.
- **Aviso pelos canais existentes.** A queda notifica por `send_monitor_error` /
  `send_monitor_recovered` com `module_id="checker"`. O contrato de quatro métodos (checado por AST)
  não muda, e o resumo do Telegram mostra uma linha só, `checker · início → fim (duração)`, na seção de
  falhas de monitoramento. `# lean: module_id sintético -> método próprio se um canal precisar tratar a
  queda de outro jeito`.
- **`event_time` real, início no payload.** O aviso sai com o `event_time` do resultado que o
  disparou, e o início real vai em `payload=[{"started_at": ...}]`, que o resumo do Telegram usa para
  datar a linha. Um `event_time` retroativo quebrava o cooldown de reenvio do resumo e o `endsAt` do
  Alertmanager (achados do bug-hunter).
- **Repetição.** A falha do `checker` aberta e anunciada é reenviada a cada
  `NOTIFICATION_REPEAT_MINUTES`, como qualquer falha de monitoramento: o Alertmanager expira o alerta
  que não é renovado. No resumo do Telegram a repetição é edição silenciosa.
- **Serialização.** `_sync_connectivity` roda sob um `asyncio.Lock`: os módulos terminam o ciclo
  juntos quando a rede volta, e sem o lock cada resultado que chega durante um envio em voo anuncia de
  novo. Uma recuperação entregue enquanto a rede caiu de novo não apaga a queda reaberta: ela recomeça
  como queda nova, devendo o próprio aviso.
- **Quando notifica.** A cada resultado, depois de atualizar o módulo, `_sync_connectivity` avalia:
  - Aberta, não anunciada, `agora` depois do fim da janela que contém o início (ou sem janela), e
    `agora - início >= tolerância`: dispara a falha do `checker`. Não entregue: tenta no próximo
    resultado (o mesmo contrato do resto do manager).
  - Fechada e anunciada: dispara a recuperação; só some do estado quando entregue.
  - Fechada sem anúncio: se o fim passou da janela e a duração atingiu a tolerância, anuncia a falha e
    depois a recuperação; senão só o log `connectivity_restored` com a duração.
  Com a rede caída o canal também falha, então na prática a falha sai quando a rede volta; a ordem
  falha → recuperação é mantida por construção.
- **Quando fecha.** Num sucesso, depois de atualizar o módulo: se a fração em erro ficou abaixo do
  quórum, a queda fecha no `event_time` desse resultado. Módulos ainda em erro e absorvidos têm
  `consecutive_errors` zerado e a marca removida: voltam ao fluxo normal contando a partir do fim.
- **Janelas.** `"HH:MM-HH:MM"` separadas por vírgula, no fuso configurado. Uma janela que cruza a
  meia-noite é aceita. O fim concreto é calculado a partir do início da queda; se o início cai em mais
  de uma janela, vale o fim mais tardio. Item mal formado ou com início igual ao fim: aviso no
  `config_fallback` e ignorado. As janelas lidas saem no log de startup.
- **Persistência.** `CHANNEL_SECTIONS` passa a aceitar `connectivity` (o store trata as seções como
  opacas). `MonitorErrorState` ganha `first_error_at` e `connectivity_loss`, lidos com default quando
  ausentes. A queda restaurada passa pelo mesmo limite de idade dos alertas
  (`NOTIFICATION_STATE_MAX_AGE_MINUTES`) e pela mesma recusa de horário no futuro. Arquivo antigo: sem a chave, sem queda. Binário antigo: ignora a chave e os campos novos.
- **Config.** Quórum fora de (0, 1], tolerância negativa e fuso inválido avisam e voltam ao default.

## Risks / Trade-offs
- Incidente real de um provedor compartilhado (CDN, DNS) que derruba 70% das status pages é lido como
  queda local e só avisa depois da tolerância, como uma linha só. Mitigação: quórum alto; sonda externa
  fica para depois.
- O primeiro módulo a atingir o limiar pode chegar antes do quórum e sair como falha individual. O
  replay com os horários reais cobre o caso medido.
- Uma janela larga demais (`00:00-23:59`) desliga o aviso de queda local o dia todo; a falha isolada
  segue protegida, e o startup lista as janelas.

- Janela num fuso com horário de verão, cujo fim cai numa hora local inexistente: o fim sai 1h
  depois. `America/Sao_Paulo` não tem horário de verão hoje; fica como lacuna conhecida.

## Migration Plan
Sem migração. O default liga a detecção com 10 minutos de tolerância e sem janelas. Rollback:
`NOTIFICATION_CONNECTIVITY_QUORUM=1` (exige 100% dos módulos) ou a imagem anterior; o `state.json`
segue compatível nos dois sentidos.
