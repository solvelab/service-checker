## ADDED Requirements

### Requirement: Liveness file reflects progress of every module
O sistema SHALL atualizar o arquivo em `SERVICE_MONITOR_LIVENESS_PATH` a cada 30 segundos somente quando todo módulo carregado completou uma checagem dentro da sua janela, igual a três intervalos do módulo mais o pior caso de uma busca, com piso de 15 minutos; uma janela não finita SHALL valer o piso. Antes da primeira checagem de um módulo, a referência SHALL ser o instante de subida do processo. Com o valor vazio, o sistema SHALL não gravar arquivo nenhum. A passagem de um módulo para atrasado, a volta ao normal e uma falha de escrita SHALL ser logadas uma vez por transição. Uma exceção na própria verificação SHALL ser logada uma vez por motivo e SHALL NOT impedir a gravação.

#### Scenario: Todos em dia
- **WHEN** todo módulo checou dentro da sua janela
- **THEN** o arquivo tem o horário de modificação atualizado

#### Scenario: Módulo pendurado
- **WHEN** um módulo não completa checagem há mais que a sua janela
- **THEN** o arquivo deixa de ser atualizado
- **AND** um evento `liveness_stale` nomeia o módulo uma única vez

#### Scenario: Largada
- **WHEN** o processo acabou de subir e nenhum módulo checou ainda
- **THEN** o arquivo é gravado enquanto a janela contada da subida não passou

#### Scenario: Canal lento
- **WHEN** a entrega serializada de notificações alonga o ciclo de um módulo para 6 minutos
- **THEN** o arquivo continua sendo atualizado

#### Scenario: Desligado
- **WHEN** `SERVICE_MONITOR_LIVENESS_PATH` está vazio
- **THEN** nenhum arquivo é gravado

### Requirement: Probe command
O comando `python -m app.healthcheck [max_age_seconds]` SHALL sair com código `0` quando o arquivo de vida existe e foi modificado há menos de `max_age_seconds` (default 120) e com código `1` em qualquer outro caso, inclusive arquivo ausente, arquivo modificado mais de `max_age_seconds` no futuro, argumento inválido ou não finito, ou liveness desligado, imprimindo o motivo em uma linha.

#### Scenario: Arquivo recente
- **WHEN** o arquivo foi modificado há 10 segundos
- **THEN** o comando sai com `0`

#### Scenario: Arquivo velho ou ausente
- **WHEN** o arquivo foi modificado há 300 segundos, ou não existe
- **THEN** o comando sai com `1` e diz o motivo
