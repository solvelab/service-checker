## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Published components
Se a fonte publicar componentes, o monitor `elevenlabs` SHALL também alertar por componente em `degraded_performance`, `partial_outage` ou `major_outage`. `ELEVENLABS_SERVICE_FILTER` nomeia regiões e SHALL NOT restringir componentes.

#### Scenario: Componente degradado
- **WHEN** a fonte publica um componente em `partial_outage`
- **THEN** ele entra como item do alerta, ao lado dos incidentes
