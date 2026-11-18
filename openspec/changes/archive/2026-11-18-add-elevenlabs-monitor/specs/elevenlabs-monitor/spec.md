## ADDED Requirements

### Requirement: ElevenLabs Status Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `elevenlabs` que consulta `https://status.elevenlabs.io/api/v2/summary.json` (incident.io, compatível com Statuspage v2) e avalia o indicador da página e os incidentes abertos.

#### Scenario: Página saudável
- **WHEN** `status.indicator` é `none` e nenhum componente publicado está degradado
- **THEN** o monitor retorna `MonitorStatus.OK`

#### Scenario: Incidente aberto
- **WHEN** há um incidente aberto cujo impacto atinge a severidade mínima configurada
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item `incident:<id>` (nome, estado e impacto) que dura enquanto o incidente estiver aberto, independente do indicador

#### Scenario: Indicador alto
- **WHEN** o indicador atinge a severidade mínima
- **THEN** o payload traz o item `page`, ao lado dos incidentes e não no lugar deles, e ele dura exatamente enquanto o indicador estiver no limiar

#### Scenario: Incidente resolvido antes de o indicador baixar
- **WHEN** um incidente aberto sob o indicador `minor` passa a `resolved`, e no ciclo seguinte o indicador volta a `none`
- **THEN** o incidente e a página alertam uma vez cada e se recuperam uma vez cada, sem um novo alerta da página no meio

#### Scenario: Indicador desconhecido
- **WHEN** `status.indicator` vem vazio ou fora de `none`, `minor`, `major`, `critical` e `maintenance`
- **THEN** o monitor retorna `MonitorStatus.ERROR`, e não `OK`, que anunciaria os incidentes abertos como restaurados

#### Scenario: Fonte sem componentes
- **WHEN** `components` vem vazio
- **THEN** o monitor avalia o indicador e os incidentes, sem `ERROR`

### Requirement: Indicator severity rule
Com a regra `status`, o monitor `elevenlabs` SHALL ler `ELEVENLABS_RULE_VALUE` como a severidade mínima, na ordem `minor` < `major` < `critical`, aplicada ao indicador da página e ao impacto de cada incidente. `none` SHALL NOT alertar.

#### Scenario: Default genérico
- **WHEN** o valor é `major,minor`, o default de `RULE_VALUE`
- **THEN** os indicadores `minor`, `major` e `critical` alertam

#### Scenario: Só o grave
- **WHEN** o valor é `major`
- **THEN** `minor` não alerta, e `major` e `critical` alertam; um incidente aberto de impacto `major` segue alertando mesmo com o indicador em `minor`

#### Scenario: Valor sem severidade conhecida
- **WHEN** o valor não nomeia nenhuma severidade
- **THEN** o monitor usa `minor` como mínimo, em vez de nunca alertar

### Requirement: Published components
Se a fonte publicar componentes, o monitor `elevenlabs` SHALL também alertar por componente em `degraded_performance`, `partial_outage` ou `major_outage`, restrito por `ELEVENLABS_SERVICE_FILTER` quando configurado.

#### Scenario: Componente degradado
- **WHEN** a fonte publica um componente em `partial_outage`
- **THEN** ele entra como item do alerta, ao lado dos incidentes

### Requirement: Controlled failure path
O monitor `elevenlabs` SHALL retornar `MonitorStatus.ERROR`, sem propagar exceção ao scheduler, quando a requisição falhar, a resposta indicar erro ou o formato não trouxer `status.indicator`.

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou HTTP de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida

#### Scenario: Formato mudou
- **WHEN** a resposta não é um objeto, ou não traz `status.indicator` como texto
- **THEN** o monitor retorna `MonitorStatus.ERROR` dizendo que o indicador faltou

#### Scenario: Incidente malformado
- **WHEN** `incidents` não é uma lista, ou traz entradas que não são objetos
- **THEN** as entradas inválidas são ignoradas, sem exceção
