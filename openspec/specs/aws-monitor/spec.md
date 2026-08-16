# aws-monitor Specification

## Purpose
Garantir visibilidade de incidentes da AWS a partir do feed público de eventos correntes. Define como
o monitor `aws` extrai a identidade de cada evento, filtra por região e decide o que é incidente.

Esta capacidade existe com requisitos explícitos porque a ausência deles saiu cara: o monitor passou
muito tempo lendo campos que o feed não publica, descartando todo evento e reportando ausência de
incidente com convicção. Não havia contrato contra o qual conferir o comportamento — apenas o código,
que estava errado e concordava consigo mesmo.

## Requirements
### Requirement: AWS Current Events Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `aws` que consome o feed público de
eventos correntes da AWS e avalia os eventos publicados.

#### Scenario: Feed sem eventos
- **WHEN** o feed não traz evento algum
- **THEN** o monitor retorna `MonitorStatus.OK` com payload vazio

#### Scenario: Evento correspondendo ao filtro e ao tipo
- **WHEN** o feed traz evento que corresponde à região monitorada e ao tipo configurado
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item por evento

### Requirement: Derive identity from the event identifier
O monitor `aws` SHALL extrair região, tipo de evento e identidade única do identificador de recurso
do evento, e SHALL NOT depender de campos de topo que o feed não publica.

#### Scenario: Evento real do feed
- **WHEN** um evento é avaliado
- **THEN** região, tipo e identidade são obtidos do identificador de recurso, que os carrega

#### Scenario: Identificador ausente ou inesperado
- **WHEN** o identificador de recurso está ausente ou não tem a forma esperada
- **THEN** a região é derivada do código de serviço, e o evento continua avaliável

### Requirement: Presence in the feed means the event is active
O monitor `aws` SHALL tratar a presença no feed de eventos correntes como o sinal de atividade, já
que a fonte não publica marca de encerramento.

#### Scenario: Critério de atividade
- **WHEN** um evento consta do feed
- **THEN** ele é considerado ativo, sem depender de campo de encerramento

### Requirement: Do not decide on undocumented severity
O monitor `aws` SHALL transportar o indicador numérico de severidade da fonte como metadado e SHALL
NOT usá-lo para decidir se há alerta, porque a fonte não documenta sua semântica.

#### Scenario: Evento com indicador de severidade
- **WHEN** um evento traz o indicador numérico
- **THEN** ele aparece no payload, e a decisão de alertar se apoia na presença no feed e no tipo do
  evento

### Requirement: Stable event identity
O monitor `aws` SHALL emitir, para cada evento, um identificador estável entre ciclos e distinto
entre eventos diferentes.

#### Scenario: Mesmo evento em dois ciclos
- **WHEN** o mesmo evento aparece em dois ciclos consecutivos
- **THEN** o identificador é idêntico nos dois

#### Scenario: Dois eventos simultâneos
- **WHEN** dois eventos distintos estão ativos ao mesmo tempo
- **THEN** eles recebem identificadores distintos, e o alerta de um não suprime o do outro

### Requirement: Region filtering
O monitor `aws` SHALL aceitar uma lista opcional que restringe a avaliação, casando contra o código
de região, o nome exibido da região ou o código de serviço.

#### Scenario: Filtro por código de região
- **WHEN** o filtro nomeia um código de região
- **THEN** apenas eventos daquela região são avaliados

#### Scenario: Filtro por nome de região
- **WHEN** o filtro nomeia a região pelo nome exibido
- **THEN** o casamento também acontece, porque o feed publica os dois

#### Scenario: Nenhum evento nas regiões monitoradas
- **WHEN** há eventos ativos, mas nenhum nas regiões do filtro
- **THEN** o monitor retorna `MonitorStatus.OK`, porque ausência de incidente nas regiões monitoradas
  é boa notícia e não erro de configuração

### Requirement: Readable incident reporting
O monitor `aws` SHALL identificar, na descrição do incidente, a região e o serviço afetados em forma
legível, e não apenas por códigos internos.

#### Scenario: Notificação de evento
- **WHEN** um evento é notificado
- **THEN** o nome exibido da região e o nome do serviço aparecem na descrição

### Requirement: Controlled failure path
O monitor `aws` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção quando a requisição falhar
ou a resposta não tiver a forma esperada.

#### Scenario: Resposta com forma inesperada
- **WHEN** a resposta chega mas não é a coleção de eventos esperada
- **THEN** o monitor retorna `MonitorStatus.ERROR` indicando payload inesperado

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou resposta de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida
