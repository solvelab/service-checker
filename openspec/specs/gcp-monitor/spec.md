# gcp-monitor Specification

## Purpose
Garantir visibilidade de incidentes do Google Cloud a partir do feed público de incidentes. Define
como o monitor `gcp` distingue incidente ativo de histórico, restringe a avaliação às regiões que
importam, e apresenta as regiões afetadas de forma legível.

## Requirements
### Requirement: GCP Incident Feed Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `gcp` que consome o feed público de
incidentes do Google Cloud e avalia os incidentes que ainda estão em curso.

#### Scenario: Nenhum incidente ativo nas regiões monitoradas
- **WHEN** nenhum incidente ativo afeta região presente no filtro
- **THEN** o monitor retorna `MonitorStatus.OK` com payload vazio

#### Scenario: Incidente ativo em região monitorada
- **WHEN** um incidente ativo afeta região presente no filtro
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item por incidente

### Requirement: Distinguish active incidents from history
O monitor `gcp` SHALL considerar ativo apenas o incidente que ainda não tem encerramento registrado e
que declara ao menos uma localização afetada no momento, porque o feed publica também o histórico.

#### Scenario: Incidente já encerrado
- **WHEN** um incidente do feed traz registro de encerramento
- **THEN** ele não é avaliado, mesmo que corresponda ao filtro e ao estado alvo

#### Scenario: Incidente sem localização afetada
- **WHEN** um incidente não declara localização afetada no momento
- **THEN** ele não é avaliado

### Requirement: Region filtering
O monitor `gcp` SHALL aceitar uma lista opcional de regiões que restringe a avaliação, casando contra
o identificador ou o nome exibido da localização afetada.

#### Scenario: Filtro ausente
- **WHEN** nenhum filtro é configurado
- **THEN** todo incidente ativo é avaliado, qualquer que seja a região

#### Scenario: Incidente fora das regiões monitoradas
- **WHEN** um incidente ativo afeta apenas regiões ausentes do filtro
- **THEN** ele não é avaliado, e a ausência de alerta significa ausência de impacto nas regiões
  monitoradas

### Requirement: Readable region reporting
O monitor `gcp` SHALL apresentar as regiões afetadas em forma legível na descrição do incidente, e
SHALL NOT expor representação interna de estrutura de dados.

#### Scenario: Incidente afetando várias regiões
- **WHEN** um incidente afeta mais de uma região
- **THEN** as regiões são apresentadas como texto legível, sem sintaxe de estrutura de dados

### Requirement: Rule strategies
O monitor `gcp` SHALL suportar avaliação pelo impacto declarado do incidente, por palavra-chave e por
expressão regular sobre a resposta serializada.

#### Scenario: Expressão regular inválida
- **WHEN** a expressão regular configurada não compila
- **THEN** o monitor retorna `MonitorStatus.ERROR` identificando o problema como regex inválida

### Requirement: Controlled failure path
O monitor `gcp` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção quando a requisição falhar
ou a resposta não tiver a forma esperada.

#### Scenario: Resposta com forma inesperada
- **WHEN** a resposta chega mas não é a coleção de incidentes esperada
- **THEN** o monitor retorna `MonitorStatus.ERROR` indicando payload inesperado

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou resposta de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida
