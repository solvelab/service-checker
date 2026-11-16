## ADDED Requirements

### Requirement: Telegram daily summary message
No modo `daily_summary` (`TELEGRAM_MODE`), o canal Telegram SHALL manter uma única mensagem por chat
por dia, no fuso `TELEGRAM_SUMMARY_TIMEZONE`, atualizada a cada evento, listando por serviço a
contagem de problemas do dia, os problemas abertos com a hora de início e os resolvidos com início,
fim e duração, e numa seção própria as falhas de monitoramento com o motivo.

#### Scenario: Repeated alert edits the day's message
- **WHEN** um alerta já aberto é reenviado pelo throttle de repetição
- **THEN** a mensagem do dia é editada, e nenhuma mensagem nova é enviada

#### Scenario: Recovery is recorded with its time
- **WHEN** um problema aberto é resolvido
- **THEN** a mensagem do dia é editada em silêncio e passa a mostrar o problema como resolvido, com a
  hora de início, a hora de resolução e a duração, no fuso do resumo

#### Scenario: Day rollover
- **WHEN** o primeiro evento acontece depois da meia-noite no fuso do resumo
- **THEN** uma mensagem nova é criada para o novo dia, os problemas ainda abertos são carregados para
  ela, e a mensagem do dia anterior não é mais editada

#### Scenario: Message over the Telegram limit
- **WHEN** o resumo do dia passa de 4096 caracteres
- **THEN** os resolvidos de cada serviço são compactados em contagem, e todos os problemas abertos
  continuam aparecendo

#### Scenario: Per-event mode
- **WHEN** `TELEGRAM_MODE` é `per_event`
- **THEN** cada evento envia um cartão próprio, como antes desta mudança

### Requirement: Only a new problem notifies on Telegram
No modo `daily_summary`, o canal Telegram SHALL produzir uma notificação no chat somente quando um
problema novo começa, reenviando a mensagem do dia para o fim do chat; repetições, recuperações e
restabelecimentos SHALL ser edições silenciosas.

#### Scenario: New problem moves the message down
- **WHEN** um componente que estava saudável entra em alerta, ou uma falha de monitoramento atinge o
  limiar
- **THEN** a mensagem do dia é apagada e reenviada atualizada, com notificação, e o chat continua com
  uma única mensagem do dia

#### Scenario: Several new problems in one burst
- **WHEN** vários problemas novos chegam dentro da janela de reenvio
- **THEN** só o primeiro reenvia com notificação, e os seguintes editam a mensagem já reenviada

### Requirement: Daily summary survives a restart
O estado do resumo diário (entradas do dia e `message_id` de cada chat) SHALL ser persistido junto com
o estado de notificação, sem mudar a versão do esquema, e restaurado no início do processo.

#### Scenario: Restart continues the same message
- **WHEN** o processo reinicia com o estado persistido, no mesmo dia
- **THEN** o próximo evento edita a mesma mensagem, sem criar outra

#### Scenario: Older state file
- **WHEN** o arquivo de estado foi escrito antes desta mudança e não tem o resumo
- **THEN** os alertas pendentes são restaurados normalmente, e o resumo começa vazio

### Requirement: Edit failures never silence a Telegram notification
Uma falha da Bot API ao editar ou apagar a mensagem do dia SHALL resultar em uma mensagem entregue
por outro caminho, ou em entrega não confirmada para o manager, nunca em sucesso sem mensagem.

#### Scenario: Content unchanged
- **WHEN** a edição é recusada com `message is not modified`
- **THEN** o canal informa entrega, porque a mensagem já mostra o conteúdo

#### Scenario: Message no longer editable
- **WHEN** a edição é recusada porque a mensagem não existe mais ou não pode ser editada
- **THEN** uma mensagem nova é enviada com o resumo, e o seu `message_id` passa a ser o do dia

#### Scenario: Delete fails before a resend
- **WHEN** apagar a mensagem do dia falha antes de um reenvio
- **THEN** o reenvio acontece mesmo assim, e a falha é registrada em log

#### Scenario: A failed ring is not downgraded
- **WHEN** o reenvio de um problema novo falha, e outro evento edita a mensagem antes do reenvio do
  manager
- **THEN** essa escrita reenvia com notificação, e o problema não fica marcado como notificado sem ter
  tocado

#### Scenario: A refused recovery edit is retried
- **WHEN** a edição que mostra uma recuperação é recusada por limite de taxa ou erro transitório
- **THEN** o fim do próximo ciclo de qualquer módulo reescreve a mensagem, sem esperar outro evento

#### Scenario: Every chat fails
- **WHEN** nenhum chat aceita a escrita
- **THEN** o canal informa que não entregou, e o manager tenta de novo no ciclo seguinte
