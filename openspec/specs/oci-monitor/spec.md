# oci-monitor Specification

## Purpose
Garantir visibilidade de incidentes da Oracle Cloud Infrastructure a partir do feed RSS oficial de
incidentes. Define como o monitor `oci` interpreta o feed, filtra por serviço ou região, decide o que
é incidente ativo e identifica cada incidente de forma estável.

A identidade estável é parte da garantia, não detalhe: o ciclo de alerta e recuperação é mantido por
incidente, e dois incidentes que compartilhem chave se suprimem mutuamente.

## Requirements
### Requirement: OCI Incident Feed Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `oci` que interpreta o feed RSS de
incidentes da OCI, extraindo de cada entrada o serviço, a região, a referência e o estado corrente.

#### Scenario: Nenhum incidente em estado alvo
- **WHEN** nenhum incidente do feed está num dos estados configurados como alvo
- **THEN** o monitor retorna `MonitorStatus.OK`

#### Scenario: Incidente em estado alvo
- **WHEN** ao menos um incidente está num dos estados alvo
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item por incidente e motivo nomeando
  região ou serviço e o estado

### Requirement: Stable incident identity
O monitor `oci` SHALL emitir, para cada incidente, um identificador estável entre ciclos e distinto
entre incidentes diferentes.

#### Scenario: Mesmo incidente em dois ciclos
- **WHEN** o mesmo incidente aparece no feed em dois ciclos consecutivos
- **THEN** o identificador é idêntico nos dois, permitindo que a repetição seja reconhecida como tal

#### Scenario: Dois incidentes simultâneos
- **WHEN** dois incidentes distintos estão ativos ao mesmo tempo
- **THEN** eles recebem identificadores distintos, e o alerta de um não suprime o do outro

#### Scenario: Entrada sem referência própria
- **WHEN** o título de uma entrada não traz a referência que a OCI normalmente publica
- **THEN** o identificador é derivado de outro campo da entrada, de forma determinística

### Requirement: Readable incident naming
O monitor `oci` SHALL emitir, para cada incidente, um nome legível, de modo que a notificação
identifique o serviço afetado e não apenas uma referência hexadecimal.

#### Scenario: Notificação de incidente
- **WHEN** um incidente é notificado
- **THEN** o nome do serviço afetado aparece na notificação

### Requirement: Incident filtering
O monitor `oci` SHALL aceitar uma lista opcional que restringe a avaliação, casando por
correspondência parcial contra título, região e serviço do incidente.

#### Scenario: Filtro por região
- **WHEN** o filtro nomeia uma região
- **THEN** apenas incidentes cujo título, região ou serviço contenham esse termo são avaliados

#### Scenario: Filtro ausente
- **WHEN** nenhum filtro é configurado
- **THEN** todos os incidentes do feed são avaliados

### Requirement: Rule strategies
O monitor `oci` SHALL suportar avaliação pelo estado do incidente, por palavra-chave e por expressão
regular sobre o corpo do feed.

#### Scenario: Expressão regular inválida
- **WHEN** a expressão regular configurada não compila
- **THEN** o monitor retorna `MonitorStatus.ERROR` identificando o problema como regex inválida

### Requirement: Controlled failure path
O monitor `oci` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção quando a requisição falhar
ou o corpo não for um feed interpretável.

#### Scenario: Feed malformado
- **WHEN** a resposta chega mas não é XML válido
- **THEN** o monitor retorna `MonitorStatus.ERROR` indicando falha de interpretação

#### Scenario: Fonte indisponível
- **WHEN** a requisição falha por rede, timeout ou resposta de erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível e duração preenchida
