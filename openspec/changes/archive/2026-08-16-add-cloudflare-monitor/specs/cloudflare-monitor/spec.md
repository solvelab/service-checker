## ADDED Requirements
### Requirement: Cloudflare Status Monitor
O sistema SHALL fornecer um monitor independente, identificado pelo slug `cloudflare`, que consulta o endpoint Statuspage v2 oficial (`https://www.cloudflarestatus.com/api/v2/summary.json`) e avalia o estado dos componentes vigiados.

#### Scenario: All watched components operational
- **WHEN** a resposta upstream lista todos os componentes vigiados com `status=operational`
- **THEN** o monitor retorna `MonitorStatus.OK` com payload contendo os componentes avaliados (`id`, `name`, `slug`, `status`)

#### Scenario: A watched component degraded
- **WHEN** um componente vigiado aparece com `status` em `degraded_performance`, `partial_outage` ou `major_outage` (ou nos estados configurados em `RULE_VALUE`)
- **THEN** o monitor retorna `MonitorStatus.ALERT` com `reason` enumerando os componentes afetados (`name: status`) e payload contendo **apenas** os componentes em alerta

### Requirement: Curated default watchlist
O monitor `cloudflare` SHALL usar uma allowlist curada quando `CLOUDFLARE_SERVICE_FILTER` estiver vazio, em vez de avaliar todos os componentes publicados.

O provedor publica 475 componentes, dos quais a maioria é um ponto de presença por cidade. Pontos de presença degradam rotineiramente sem impacto — a rede reroteia — e avaliá-los produziria dezenas de alertas por dia. Esta é uma divergência deliberada do comportamento dos demais monitores Statuspage, onde filtro vazio significa avaliar tudo.

#### Scenario: Filter omitted
- **WHEN** `service_filter` é vazio ou contém apenas entradas em branco
- **THEN** o monitor avalia exatamente `Tunnel`, `Authoritative DNS`, `Network`, `CDN/Cache` e `SSL Certificate Provisioning`

#### Scenario: Point of presence degraded under the default watchlist
- **WHEN** o filtro é o default e um ou mais pontos de presença estão em `partial_outage` ou `under_maintenance`, sem nenhum produto vigiado degradado
- **THEN** o monitor retorna `MonitorStatus.OK` e nenhuma notificação é emitida

#### Scenario: Operator opts into every component
- **WHEN** `CLOUDFLARE_SERVICE_FILTER` contém a entrada `*`
- **THEN** o monitor avalia todos os componentes publicados, pontos de presença inclusive

#### Scenario: Explicit filter replaces the default
- **WHEN** `CLOUDFLARE_SERVICE_FILTER` lista um ou mais componentes
- **THEN** o monitor avalia apenas esses, e a allowlist default não é aplicada

### Requirement: Watched component missing from payload is reported
O monitor `cloudflare` SHALL registrar em log (nível WARNING, evento `component_missing`) os nomes vigiados que não aparecem no payload upstream, nomeando cada um.

Uma renomeação upstream encolheria a lista de vigiados em silêncio: o monitor seguiria retornando `OK`, correto a respeito dos componentes que ainda casam, que é exatamente como um monitor fica cego parecendo saudável.

#### Scenario: Renamed component
- **WHEN** um nome da allowlist não corresponde a nenhum `id`, `slug` ou `name` do payload
- **THEN** o monitor emite um log WARNING `watched component not found in status payload` citando os nomes ausentes, e segue avaliando os que casaram

#### Scenario: Watchlist fully present
- **WHEN** todos os nomes vigiados casam com algum componente
- **THEN** nenhum log de ausência é emitido

#### Scenario: No watched component matches
- **WHEN** nenhum nome vigiado casa com componente algum
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` "no target components matched filter" e payload contendo o filtro e os componentes recebidos

### Requirement: Rule Strategies
O monitor `cloudflare` SHALL suportar três estratégias de avaliação selecionáveis via `CLOUDFLARE_RULE_KIND`: `status` (default), `keyword` e `regex`.

#### Scenario: Status rule with empty value uses safe defaults
- **WHEN** `RULE_KIND=status` e `RULE_VALUE` é vazio
- **THEN** o monitor utiliza o conjunto default `{degraded_performance, partial_outage, major_outage}`, que deliberadamente não inclui `under_maintenance`

#### Scenario: Keyword rule match
- **WHEN** `RULE_KIND=keyword` e o texto do JSON da resposta contém `RULE_VALUE` (case-insensitive)
- **THEN** o monitor retorna `MonitorStatus.ALERT` com `reason` indicando a keyword detectada

#### Scenario: Regex rule with invalid pattern
- **WHEN** `RULE_KIND=regex` e `RULE_VALUE` não compila como regex válida
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando "invalid regex"

### Requirement: Incident and Maintenance Enrichment
Quando um alerta é gerado, o monitor `cloudflare` SHALL buscar `api/v2/incidents/unresolved.json` e `api/v2/scheduled-maintenances/active.json` no mesmo host para enriquecer `reason`, limitado a 3 de cada.

#### Scenario: Active incident enriches alert reason
- **WHEN** há alerta de componente e o endpoint de incidentes retorna ao menos um incidente não resolvido
- **THEN** o `reason` final contém o segmento `Incident: <name> (<status>, <timestamp>)` concatenado por `; ` ao motivo base

#### Scenario: Nothing to enrich leaves the reason unchanged
- **WHEN** os endpoints de enriquecimento respondem sem incidentes nem manutenções
- **THEN** o `reason` permanece idêntico ao produzido pela regra, sem troca de separador

#### Scenario: Enrichment fetch failure is non-fatal
- **WHEN** as requisições de enriquecimento falham (rede, HTTP ≥ 400, JSON inválido)
- **THEN** o monitor ainda retorna `MonitorStatus.ALERT` com o `reason` base, sem propagar a exceção

### Requirement: Controlled Failure Path
O monitor `cloudflare` SHALL retornar `MonitorStatus.ERROR` — nunca propagar exceção para o scheduler — em falha de rede, timeout, HTTP ≥ 400 ou payload inválido, registrando `reason` legível e `duration_ms`.

Propagar exceção não é apenas feio: o scheduler a captura, mas o módulo nunca chega a devolver um `MonitorStatus.ERROR`, então a notificação de monitor morto não dispara e a quebra vive só no log.

#### Scenario: Upstream timeout
- **WHEN** a requisição excede `timeout_seconds`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando o timeout e o scheduler segue executando os demais monitores

#### Scenario: Empty components in payload
- **WHEN** a resposta retorna 200 mas `components` está vazio ou ausente
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` "no components in status response"

#### Scenario: Malformed components
- **WHEN** `components` chega como dicionário, ou como lista contendo valores que não são objetos
- **THEN** o monitor retorna `MonitorStatus.ERROR` em vez de levantar exceção

### Requirement: Configuration Defaults
O monitor `cloudflare` SHALL expor `enabled`, `interval_seconds`, `timeout_seconds`, `user_agent`, `rule.kind`, `rule.value` e `service_filter` via env vars com prefixo `CLOUDFLARE_`.

#### Scenario: Minimal configuration
- **WHEN** `SERVICE_MONITOR_MODULES` inclui `cloudflare` e nenhuma env var `CLOUDFLARE_*` é definida
- **THEN** o monitor inicializa com URL `https://www.cloudflarestatus.com/api/v2/summary.json`, intervalo e timeout default, regra `status` com os três estados de outage, e a allowlist curada
