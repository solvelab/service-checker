# rockstar-monitor Specification

## Purpose
TBD - created by archiving change replace-cfx-with-rockstar-monitor. Update Purpose after archive.
## Requirements
### Requirement: Rockstar Services Status Monitor
O sistema SHALL fornecer um monitor independente, identificado pelo slug `rockstar`, que avalia o estado dos serviços oficiais da Rockstar Games (incluindo FiveM e RedM, anteriormente cobertos pelo monitor `cfx`) consultando a fonte de dados oficial usada por `https://support.rockstargames.com/servicestatus`.

#### Scenario: All filtered services healthy
- **WHEN** a resposta upstream lista todos os serviços em `service_filter` com severidade equivalente a "operacional"
- **THEN** o monitor retorna `MonitorStatus.OK` com payload contendo a lista de serviços filtrados e seus estados normalizados

#### Scenario: At least one filtered service degraded
- **WHEN** a resposta upstream lista qualquer serviço em `service_filter` em estado degradado, parcialmente fora do ar ou em manutenção
- **THEN** o monitor retorna `MonitorStatus.ALERT` com `reason` enumerando os serviços afetados e seus estados, e payload incluindo apenas os serviços filtrados

### Requirement: Service Filtering
O monitor `rockstar` SHALL aceitar uma lista opcional `service_filter` (configuração) que limita a avaliação a um subconjunto de serviços identificados por nome canônico (case-insensitive).

#### Scenario: Filter omitted
- **WHEN** `service_filter` não é fornecido
- **THEN** todos os serviços retornados pelo upstream são avaliados

#### Scenario: Filter does not match any service
- **WHEN** `service_filter` é fornecido e nenhum serviço retornado bate com a lista
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` indicando "no target services matched filter" e payload contendo o filtro e os serviços recebidos

### Requirement: Severity Normalization
O monitor `rockstar` SHALL normalizar as severidades upstream para `OK`, `ALERT` ou `ERROR` segundo a matriz documentada em `design.md`, com precedência: qualquer serviço filtrado em estado não-operacional eleva o resultado para `ALERT`.

#### Scenario: Mixed states with one degraded
- **WHEN** a resposta upstream contém serviços operacionais e ao menos um em estado degradado dentro do filtro
- **THEN** o monitor retorna `MonitorStatus.ALERT`

### Requirement: Controlled Failure Path
O monitor `rockstar` SHALL retornar `MonitorStatus.ERROR` (sem propagar exceções para o scheduler) em casos de falha de rede, timeout, status HTTP ≥ 400 ou payload inválido/inesperado, registrando `reason` legível e `duration_ms`.

#### Scenario: Upstream timeout
- **WHEN** a requisição ao endpoint excede `timeout_seconds`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando timeout e o scheduler segue executando os demais monitores

#### Scenario: Malformed payload
- **WHEN** a resposta retorna 200 mas o corpo não corresponde ao schema esperado (ex.: campo de serviços ausente)
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` indicando parse failure

### Requirement: Configuration Defaults
O monitor `rockstar` SHALL expor as opções de configuração `enabled`, `interval_seconds`, `timeout_seconds`, `retries` e `service_filter`, com defaults seguros que permitem habilitar o monitor apenas configurando `enabled: true`.

#### Scenario: Minimal configuration
- **WHEN** a config define apenas `enabled: true` para o slug `rockstar`
- **THEN** o monitor inicializa com a URL oficial default, intervalo e timeout default, sem filtro de serviço, e começa a publicar resultados a cada ciclo

### Requirement: Observability
O monitor `rockstar` SHALL emitir logs estruturados a cada execução contendo `module_id=rockstar`, `event=monitor_check`, `status`, `reason` (quando aplicável) e `duration_ms`, consistente com os demais monitores.

#### Scenario: Successful check log
- **WHEN** o monitor executa com sucesso
- **THEN** um log com `level=INFO`, `module_id=rockstar`, `status=OK` e `duration_ms` numérico é emitido

#### Scenario: Failed check log
- **WHEN** o monitor falha por qualquer motivo
- **THEN** um log com `level=ERROR`, `module_id=rockstar`, `status=ERROR`, `reason` preenchido e `duration_ms` numérico é emitido
