## ADDED Requirements

### Requirement: Retell Status Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `retell` que consulta o resumo da Statuspage da Retell AI (`https://status.retellai.com/api/v2/summary.json`) e avalia o estado dos componentes retornados.

#### Scenario: Todos os componentes operacionais
- **WHEN** nenhum componente avaliado está num dos estados alvo
- **THEN** o monitor retorna `MonitorStatus.OK`, com payload contendo os componentes avaliados

#### Scenario: Componente degradado
- **WHEN** ao menos um componente avaliado está num estado alvo, como `degraded_performance`, `partial_outage` ou `major_outage`
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com `reason` listando `<nome>: <estado>` de cada componente afetado

### Requirement: Rule strategies
O monitor `retell` SHALL suportar avaliação por estado do componente (`status`), por palavra-chave (`keyword`) e por expressão regular (`regex`) sobre a resposta serializada.

#### Scenario: Valor da regra sem estado de componente
- **WHEN** a regra é `status` e o valor configurado não nomeia nenhum estado de componente da Statuspage, como o default genérico `major,minor`
- **THEN** o monitor avalia contra `degraded_performance`, `partial_outage` e `major_outage`, em vez de nunca alertar

#### Scenario: Expressão regular inválida
- **WHEN** a expressão regular configurada não compila
- **THEN** o monitor retorna `MonitorStatus.ERROR` identificando a regex inválida

### Requirement: Component filtering
O monitor `retell` SHALL aceitar `RETELL_SERVICE_FILTER`, que restringe a avaliação a componentes por identificador, nome normalizado ou nome exibido.

#### Scenario: Filtro ausente
- **WHEN** nenhum filtro é configurado
- **THEN** todos os componentes retornados são avaliados

#### Scenario: Filtro sem correspondência
- **WHEN** um filtro é configurado e nenhum componente corresponde
- **THEN** o monitor retorna `MonitorStatus.ERROR`, com o filtro e os componentes disponíveis no payload

### Requirement: Incident and Maintenance Enrichment
Num alerta, o monitor `retell` SHALL buscar `api/v2/incidents/unresolved.json` e `api/v2/scheduled-maintenances/active.json` no mesmo host para enriquecer o `reason`.

#### Scenario: Incidente ativo
- **WHEN** há alerta de componente e um incidente não resolvido
- **THEN** o `reason` traz o segmento do incidente, com nome, estado e horário, junto ao motivo do componente

#### Scenario: Falha do enriquecimento
- **WHEN** as requisições de enriquecimento falham
- **THEN** o monitor ainda retorna `MonitorStatus.ALERT` com o motivo do componente, sem propagar a exceção

### Requirement: Controlled failure path
O monitor `retell` SHALL retornar `MonitorStatus.ERROR`, sem propagar exceção ao scheduler, quando a requisição falhar, a resposta indicar erro ou o payload não trouxer componente algum.

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou HTTP de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida

#### Scenario: Resposta sem componentes
- **WHEN** a resposta chega sem componente algum
- **THEN** o monitor retorna `MonitorStatus.ERROR`, porque ausência de dados não é evidência de saúde

### Requirement: Configuration defaults
O monitor `retell` SHALL ler a configuração das env vars com prefixo `RETELL_` e SHALL funcionar só com `retell` em `SERVICE_MONITOR_MODULES`.

#### Scenario: Configuração mínima
- **WHEN** `SERVICE_MONITOR_MODULES` inclui `retell` e nenhuma `RETELL_*` é definida
- **THEN** o monitor usa a URL default, sem filtro, e alerta quando um componente degrada
