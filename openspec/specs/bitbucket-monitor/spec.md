# bitbucket-monitor Specification

## Purpose
Garantir visibilidade de incidentes do Bitbucket Cloud consumindo a Statuspage oficial da Atlassian. Define como o monitor `bitbucket` avalia o estado dos componentes, restringe a avaliação a um subconjunto deles, enriquece o alerta com incidentes e manutenções em aberto, e degrada de forma controlada quando o upstream falha.

## Requirements
### Requirement: Bitbucket Status Monitor
O sistema SHALL fornecer um monitor independente, identificado pelo slug `bitbucket`, que consulta o endpoint Statuspage v2 oficial da Atlassian (`https://bitbucket.status.atlassian.com/api/v2/summary.json`) e avalia o estado dos componentes do Bitbucket Cloud.

#### Scenario: All components operational
- **WHEN** a resposta upstream lista todos os componentes (após filtro opcional) com `status=operational`
- **THEN** o monitor retorna `MonitorStatus.OK` com payload contendo a lista de componentes avaliados (`id`, `name`, `slug`, `status`)

#### Scenario: At least one component degraded
- **WHEN** a resposta upstream lista qualquer componente filtrado com `status` em `degraded_performance`, `partial_outage` ou `major_outage` (ou nos estados configurados em `RULE_VALUE`)
- **THEN** o monitor retorna `MonitorStatus.ALERT` com `reason` enumerando os componentes afetados (`name: status`) e payload contendo apenas os componentes em alerta

### Requirement: Rule Strategies
O monitor `bitbucket` SHALL suportar três estratégias de avaliação selecionáveis via `BITBUCKET_RULE_KIND`: `status` (default), `keyword` e `regex`.

#### Scenario: Status rule with empty value uses safe defaults
- **WHEN** `RULE_KIND=status` e `RULE_VALUE` é vazio
- **THEN** o monitor utiliza o conjunto default `{degraded_performance, partial_outage, major_outage}`

#### Scenario: Keyword rule match
- **WHEN** `RULE_KIND=keyword` e o texto do JSON da resposta contém `RULE_VALUE` (case-insensitive)
- **THEN** o monitor retorna `MonitorStatus.ALERT` com `reason` indicando a keyword detectada

#### Scenario: Regex rule with invalid pattern
- **WHEN** `RULE_KIND=regex` e `RULE_VALUE` não compila como regex válida
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando "invalid regex"

### Requirement: Component Filtering
O monitor `bitbucket` SHALL aceitar `BITBUCKET_SERVICE_FILTER` (lista separada por vírgulas de `id`, `slug` ou `name`, case-insensitive) para restringir a avaliação a um subconjunto de componentes.

#### Scenario: Filter omitted
- **WHEN** `service_filter` é vazio
- **THEN** todos os componentes retornados pelo upstream são avaliados

#### Scenario: Filter does not match any component
- **WHEN** `service_filter` é fornecido e nenhum componente bate com a lista
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` "no target components matched filter" e payload contendo o filtro e os componentes recebidos

### Requirement: Incident and Maintenance Enrichment
Quando um alerta é gerado, o monitor `bitbucket` SHALL buscar `api/v2/incidents/unresolved.json` e `api/v2/scheduled-maintenances/active.json` no mesmo host para enriquecer `reason` com detalhes de incidentes e manutenções ativas.

#### Scenario: Active incident enriches alert reason
- **WHEN** há alerta de componente e o endpoint de incidentes retorna ao menos um incidente não resolvido
- **THEN** o `reason` final contém o segmento `Incident: <name> (<status>, <timestamp>)` concatenado por `; ` ao motivo base, limitado a 3 incidentes

#### Scenario: Enrichment fetch failure is non-fatal
- **WHEN** as requisições de enriquecimento falham (rede, HTTP ≥ 400, JSON inválido)
- **THEN** o monitor ainda retorna `MonitorStatus.ALERT` com o `reason` base do componente, sem propagar a exceção

### Requirement: Controlled Failure Path
O monitor `bitbucket` SHALL retornar `MonitorStatus.ERROR` (sem propagar exceções para o scheduler) em casos de falha de rede, timeout, status HTTP ≥ 400 ou payload inválido, registrando `reason` legível e `duration_ms`.

#### Scenario: Upstream timeout
- **WHEN** a requisição ao endpoint de summary excede `timeout_seconds`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando o timeout e o scheduler segue executando os demais monitores

#### Scenario: Empty components in payload
- **WHEN** a resposta retorna 200 mas `components` está vazio ou ausente
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` "no components in status response"

### Requirement: Configuration Defaults
O monitor `bitbucket` SHALL expor as opções de configuração `enabled`, `interval_seconds`, `timeout_seconds`, `user_agent`, `rule.kind`, `rule.value` e `service_filter` via env vars com prefixo `BITBUCKET_`, com defaults seguros que permitem habilitar o monitor apenas adicionando `bitbucket` a `SERVICE_MONITOR_MODULES`.

#### Scenario: Minimal configuration
- **WHEN** `SERVICE_MONITOR_MODULES` inclui `bitbucket` e nenhuma env var `BITBUCKET_*` é definida
- **THEN** o monitor inicializa com URL `https://bitbucket.status.atlassian.com/api/v2/summary.json`, intervalo e timeout default, sem filtro, regra `status` com value `major,minor` e começa a publicar resultados a cada ciclo

