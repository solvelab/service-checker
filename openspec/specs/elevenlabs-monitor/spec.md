# elevenlabs-monitor Specification

## Purpose
Garantir visibilidade de incidentes da plataforma de voz da ElevenLabs lendo a página de status
incident.io (`api/v2/summary.json`), que não publica componentes: o indicador da página e cada
incidente aberto viram itens de alerta, cada um com seu próprio aviso e sua própria recuperação.
## Requirements
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
Se a fonte publicar componentes, o monitor `elevenlabs` SHALL também alertar por componente em `degraded_performance`, `partial_outage` ou `major_outage`. `ELEVENLABS_SERVICE_FILTER` nomeia regiões e SHALL NOT restringir componentes.

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

### Requirement: Incident regions
Quando o `summary.json` tiver incidente aberto, o monitor `elevenlabs` SHALL buscar o catálogo de regiões em `/proxy/<host>` e os incidentes de cada região em `/proxy/<host>/incidents?subpage_slug=<subpath>`, e SHALL acrescentar a cada item `incident:<id>` a lista `regions` com os nomes das regiões que listam aquele `id`. O texto do alerta SHALL ser `<nome> [<regiões>]: <status>` quando houver região conhecida. Um incidente em várias regiões SHALL continuar sendo um item só. Sem incidente aberto que alcance a severidade da regra e sem filtro, o monitor SHALL NOT fazer requisição além do `summary.json`. As regiões vistas para um incidente SHALL ser lembradas enquanto ele estiver aberto, e uma região que falhe SHALL NOT descartar as respostas das outras.

#### Scenario: Incidente em três regiões
- **WHEN** o incidente aberto `Degraded performance of Voice Library voices` aparece nas regiões `eu`, `india` e `singapore`
- **THEN** o alerta diz `Degraded performance of Voice Library voices [EU (Enterprise-only), India (Enterprise-only), Singapore (Enterprise-only)]: investigating`
- **AND** há um item só para o incidente

#### Scenario: Página saudável
- **WHEN** não há incidente aberto e o filtro está vazio
- **THEN** só o `summary.json` é pedido

### Requirement: Region filter
`ELEVENLABS_SERVICE_FILTER` SHALL nomear regiões, por `subpath` ou nome, sem diferenciar caixa; vazio SHALL significar todas. Com filtro, o monitor SHALL alertar só incidente listado em pelo menos uma região filtrada e SHALL NOT emitir o item `page`. Um valor do filtro ausente do catálogo SHALL resultar em `ERROR` com o valor no motivo. A decisão de incluir ou não um incidente SHALL ser tomada na primeira vez que ele aparece e SHALL valer enquanto ele estiver aberto; um incidente de região ainda desconhecida SHALL ser incluído.

#### Scenario: Incidente fora do filtro
- **WHEN** o filtro é `united-states` e o único incidente aberto está só em `eu`
- **THEN** o resultado é `OK`

#### Scenario: Busca oscilando
- **WHEN** o filtro é `united-states`, um incidente só de `eu` foi deixado de fora, e no ciclo seguinte a busca de regiões falha
- **THEN** o incidente continua fora, sem alerta e sem recuperação

#### Scenario: Filtro inválido
- **WHEN** o filtro é `brazil` e o catálogo não tem essa região
- **THEN** o resultado é `ERROR` com motivo nomeando `brazil`

### Requirement: Region lookup fallback
Se o catálogo ou os incidentes por região não puderem ser lidos (erro de rede, status HTTP de erro, formato inesperado), o monitor SHALL alertar os incidentes com as regiões já conhecidas ou sem região, SHALL registrar um `WARNING` com `event` `region_lookup_failed` uma vez por sequência de falhas e um `region_lookup_recovered` na volta, e, com filtro, SHALL alertar o incidente mesmo sem saber a região. Severidade e ciclo de vida dos itens SHALL continuar vindo do `summary.json`.

#### Scenario: Proxy fora do ar
- **WHEN** há incidente aberto e `/proxy/<host>` responde 503
- **THEN** o alerta sai sem região
- **AND** o log traz `region_lookup_failed`

### Requirement: Page indicator item cites the open incidents
O item do indicador da página (`id` "page") SHALL levar em `incidents` os títulos, até três, dos incidentes abertos que alertam neste ciclo, já com as regiões quando conhecidas.

#### Scenario: Indicador com incidente
- **WHEN** o indicador está em `minor` e há um incidente aberto "Degraded performance of Voice Library voices" na região EU
- **THEN** o item do indicador traz `incidents` ["Degraded performance of Voice Library voices [EU]"]

