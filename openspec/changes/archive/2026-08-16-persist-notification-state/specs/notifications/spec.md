## ADDED Requirements
### Requirement: Pending alerts survive a process restart
O sistema SHALL persistir os alertas pendentes e o estado de erro de monitor quando `NOTIFICATION_STATE_PATH` estiver configurado, de modo que um incidente iniciado antes de um restart ainda produza a notificação de recuperação depois dele.

Sem essa persistência o all-clear se perde em silêncio: nenhum provedor publica a mesma degradação duas vezes, então após o restart o payload saudável não representa transição alguma.

#### Scenario: Incident spans a restart
- **WHEN** um componente alerta, o processo reinicia, e em seguida o provedor volta ao normal
- **THEN** a notificação de recuperação é emitida, citando o componente

#### Scenario: No state path configured
- **WHEN** `NOTIFICATION_STATE_PATH` é vazio ou ausente
- **THEN** o estado é mantido apenas em memória e o comportamento é idêntico ao anterior a esta mudança

#### Scenario: Throttle is preserved
- **WHEN** um alerta é emitido, o processo reinicia, e o componente segue degradado dentro da janela de `NOTIFICATION_REPEAT_MINUTES`
- **THEN** nenhum alerta novo é emitido, porque o instante do último envio foi restaurado junto

#### Scenario: Component already recovered before the restart
- **WHEN** o alerta já havia sido resolvido antes do restart
- **THEN** nada é restaurado para aquele componente e nenhuma recuperação é emitida

### Requirement: Stale state is discarded rather than replayed
O sistema SHALL descartar, na subida, todo alerta persistido mais antigo que `NOTIFICATION_STATE_MAX_AGE_MINUTES`, sem emitir notificação por ele.

Uma recuperação tardia demais informa menos do que confunde: o operador receberia o "resolvido" de um incidente que já não lembra.

#### Scenario: Alert older than the limit
- **WHEN** o estado contém um alerta gravado há mais tempo que o limite configurado
- **THEN** ele é descartado na carga e nenhum all-clear é emitido para ele

#### Scenario: Limit disabled
- **WHEN** `NOTIFICATION_STATE_MAX_AGE_MINUTES` é `0`
- **THEN** nenhum alerta é descartado por idade

#### Scenario: Timestamp in the future
- **WHEN** um alerta persistido tem instante mais de cinco minutos à frente do relógio atual
- **THEN** ele é descartado, porque congelaria a janela de repetição indefinidamente

### Requirement: Persistence never prevents monitoring
O sistema SHALL tratar toda falha de leitura ou escrita do estado como registrável e não-fatal, seguindo a execução com o estado que conseguiu obter.

#### Scenario: Unreadable or corrupt state file
- **WHEN** o arquivo não existe, está vazio, contém JSON inválido, não é um objeto, ou declara versão de schema diferente
- **THEN** o processo sobe com estado vazio, registra o motivo em log, e segue monitorando

#### Scenario: State cannot be written
- **WHEN** a escrita falha por permissão, disco cheio ou caminho inválido
- **THEN** o ciclo de verificação e as notificações prosseguem normalmente, e a falha é registrada

#### Scenario: Write is interrupted
- **WHEN** a escrita é interrompida no meio
- **THEN** o arquivo anterior permanece íntegro, porque a gravação ocorre em arquivo temporário seguido de rename atômico

#### Scenario: One corrupt entry among valid ones
- **WHEN** uma entrada do arquivo é inválida e as demais são válidas
- **THEN** apenas a inválida é descartada, e as válidas são restauradas
