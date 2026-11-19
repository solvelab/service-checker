## ADDED Requirements

### Requirement: Startup announcement
O sistema SHALL enviar a cada canal humano ligado (Telegram, webhook, Google Chat) uma única mensagem de subida. Ela sai quando todo módulo carregado tiver entregue o primeiro resultado, ou quando vencer `NOTIFICATION_STARTUP_TIMEOUT_SECONDS`, o que vier primeiro. A mensagem SHALL trazer a versão, o nome do pod, os canais ligados e, por módulo, o status do primeiro resultado (`OK`, `ALERT` ou `ERROR`) com o motivo quando houver. O aviso SHALL ser desligável por `NOTIFICATION_STARTUP_ANNOUNCE`.

#### Scenario: Todos os módulos respondem
- **WHEN** o checker sobe e cada módulo carregado entrega o primeiro resultado antes do prazo
- **THEN** cada canal humano recebe uma mensagem de subida com versão, pod, canais e o status de cada módulo

#### Scenario: Módulo em erro na primeira checagem
- **WHEN** a primeira checagem de um módulo dá `ERROR`, por exemplo `no target components matched filter`
- **THEN** a mensagem de subida mostra esse módulo em `ERROR` com o motivo, sem esperar o limiar de falhas

#### Scenario: Módulo lento
- **WHEN** um módulo não entrega resultado até `NOTIFICATION_STARTUP_TIMEOUT_SECONDS`
- **THEN** a mensagem sai no prazo, e o módulo aparece como sem resultado

#### Scenario: Aviso desligado
- **WHEN** `NOTIFICATION_STARTUP_ANNOUNCE` é `false`
- **THEN** nenhuma mensagem de subida é enviada

### Requirement: Daily heartbeat
O sistema SHALL enviar aos canais humanos ligados uma mensagem por dia, em `NOTIFICATION_HEARTBEAT_TIME` no fuso `NOTIFICATION_HEARTBEAT_TIMEZONE`, com o tempo no ar e o último status de cada módulo. O primeiro heartbeat SHALL ser a próxima ocorrência do horário depois da subida, sem compensar o dia perdido. `NOTIFICATION_HEARTBEAT_TIME` vazio SHALL desligar o heartbeat.

#### Scenario: Horário atingido
- **WHEN** o relógio chega a `NOTIFICATION_HEARTBEAT_TIME` no fuso configurado
- **THEN** cada canal humano recebe um heartbeat com o tempo no ar e o último status de cada módulo

#### Scenario: Restart depois do heartbeat do dia
- **WHEN** o checker reinicia no mesmo dia, depois de já ter enviado o heartbeat
- **THEN** o próximo heartbeat é o do dia seguinte

#### Scenario: Subida depois do horário
- **WHEN** o checker sobe depois de `NOTIFICATION_HEARTBEAT_TIME`
- **THEN** o primeiro heartbeat é o do dia seguinte, e o aviso de subida cobre o dia corrente

#### Scenario: Horário inválido
- **WHEN** `NOTIFICATION_HEARTBEAT_TIME` não é `HH:MM` válido
- **THEN** a subida registra um aviso de configuração e usa o default `09:00`

### Requirement: Status reports reach only human channels and report delivery
A mensagem de subida e o heartbeat SHALL ir só aos canais que entregam a humano: Telegram, webhook e Google Chat. O Alertmanager SHALL ficar de fora. Cada canal SHALL reportar se entregou. Um relatório que nenhum canal aceitou SHALL ser tentado de novo, até 3 tentativas, espaçadas de 60s. A falha de um canal SHALL ser logada com o canal e o motivo, sem impedir os outros canais nem o monitoramento. Nenhuma credencial (URL de webhook, token) SHALL aparecer no log nem na mensagem.

#### Scenario: Um canal falha
- **WHEN** o Google Chat recusa a mensagem de subida e o Telegram a aceita
- **THEN** a falha do Google Chat é logada com o motivo, o Telegram recebe a mensagem, e não há nova tentativa

#### Scenario: Nenhum canal aceita
- **WHEN** todos os canais humanos recusam a mensagem de subida
- **THEN** ela é tentada de novo, até 3 tentativas no total, e cada falha é logada

#### Scenario: Telegram em resumo diário
- **WHEN** o Telegram está em `daily_summary`
- **THEN** a mensagem de subida e o heartbeat chegam como mensagens novas, e o resumo do dia não muda
