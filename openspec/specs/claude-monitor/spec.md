# claude-monitor Specification

## Purpose
Garantir visibilidade de incidentes dos serviços Claude consumindo a Statuspage oficial da
Anthropic. Define como o monitor `claude` avalia o estado dos componentes publicados, restringe a
avaliação a um subconjunto e degrada de forma controlada quando a fonte falha.

## Requirements
### Requirement: Claude Status Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `claude` que consulta o resumo de estado
publicado pela Statuspage da Anthropic e avalia o estado dos componentes retornados.

#### Scenario: Todos os componentes operacionais
- **WHEN** nenhum componente avaliado está num dos estados configurados como alvo
- **THEN** o monitor retorna `MonitorStatus.OK` com payload contendo os componentes avaliados

#### Scenario: Componente degradado
- **WHEN** ao menos um componente avaliado está num dos estados alvo
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item por componente afetado e motivo
  nomeando cada um

### Requirement: Component identity for per-component lifecycle
O monitor `claude` SHALL emitir, para cada componente, identificador, nome e forma normalizada do
nome, de modo que o ciclo de alerta e recuperação seja mantido por componente e não pelo provedor
inteiro.

#### Scenario: Um componente se recupera enquanto outro segue degradado
- **WHEN** dois componentes estão em alerta e apenas um retorna ao estado operacional
- **THEN** a recuperação identifica esse componente, e o outro permanece em alerta

### Requirement: Component filtering
O monitor `claude` SHALL aceitar uma lista opcional que restringe a avaliação a componentes
específicos, aceitando identificador, nome normalizado ou nome exibido.

#### Scenario: Filtro ausente
- **WHEN** nenhum filtro é configurado
- **THEN** todos os componentes retornados pela fonte são avaliados

#### Scenario: Filtro sem correspondência
- **WHEN** um filtro é configurado e nenhum componente corresponde
- **THEN** o monitor retorna `MonitorStatus.ERROR`, com payload contendo o filtro recebido e os
  componentes disponíveis, para que o operador consiga corrigir a configuração

### Requirement: Rule strategies
O monitor `claude` SHALL suportar avaliação por estado estruturado do componente, por palavra-chave e
por expressão regular sobre a resposta serializada.

#### Scenario: Expressão regular inválida
- **WHEN** a expressão regular configurada não compila
- **THEN** o monitor retorna `MonitorStatus.ERROR` identificando o problema como regex inválida

#### Scenario: Estratégia desconhecida
- **WHEN** a estratégia configurada não é uma das suportadas
- **THEN** o monitor retorna `MonitorStatus.ERROR` nomeando a estratégia recebida

### Requirement: Controlled failure path
O monitor `claude` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção ao scheduler quando a
requisição falhar, a resposta indicar erro, ou a resposta não contiver componente algum.

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou resposta de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida

#### Scenario: Resposta sem componentes
- **WHEN** a resposta chega mas não traz componente algum
- **THEN** o monitor retorna `MonitorStatus.ERROR`, e não `OK`, porque ausência de dados não é
  evidência de saúde
