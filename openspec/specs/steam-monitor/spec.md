# steam-monitor Specification

## Purpose
Garantir visibilidade dos serviços da Steam a partir de `steamstat.us`, que é uma página HTML atrás
de Cloudflare e não uma API. Define como o monitor `steam` obtém a página, extrai os serviços,
decide o que é incidente e degrada quando não consegue avaliar.

O transporte faz parte da garantia, não é detalhe de implementação: a fonte bloqueia por impressão
digital de TLS, então trocar o cliente HTTP quebra o monitor por inteiro.

## Requirements
### Requirement: Steam Services Status Monitor
O sistema SHALL fornecer um monitor identificado pelo slug `steam` que avalia o estado dos serviços
publicados em `https://steamstat.us/`, extraindo nome, identificador, severidade e texto de estado
de cada serviço listado.

#### Scenario: Todos os serviços operacionais
- **WHEN** a página lista todos os serviços com severidade equivalente a operacional
- **THEN** o monitor retorna `MonitorStatus.OK` com payload contendo os serviços avaliados

#### Scenario: Serviço degradado
- **WHEN** ao menos um serviço avaliado tem severidade entre as configuradas como alvo
- **THEN** o monitor retorna `MonitorStatus.ALERT`, com um item por serviço afetado

### Requirement: TLS impersonation transport
O monitor `steam` SHALL obter a página por um cliente que imite a impressão digital de TLS de um
navegador real, porque a fonte está atrás de um WAF que recusa clientes HTTP comuns
independentemente dos cabeçalhos enviados.

#### Scenario: Cliente HTTP comum é recusado
- **WHEN** a requisição parte de um cliente sem imitação de TLS
- **THEN** a fonte responde com erro de autorização, qualquer que seja o `User-Agent`

#### Scenario: Perfil de imitação configurável
- **WHEN** o perfil de imitação em uso passa a ser recusado pela fonte
- **THEN** o operador pode apontar outro perfil por configuração, sem alteração de código

### Requirement: Service filtering by identifier
O monitor `steam` SHALL aceitar uma lista opcional de identificadores de serviço que restringe a
avaliação a esse subconjunto.

#### Scenario: Filtro ausente
- **WHEN** nenhum filtro é configurado
- **THEN** todos os serviços da página são avaliados, exceto os explicitamente ignorados

#### Scenario: Filtro sem correspondência
- **WHEN** um filtro é configurado e nenhum serviço da página corresponde
- **THEN** o monitor retorna `MonitorStatus.ERROR`, porque um filtro que não casa com nada indica
  configuração errada e não ausência de incidente

### Requirement: Ignore non-service entries
O monitor `steam` SHALL ignorar entradas da página que não representam estado de serviço, para que
métricas de tráfego não sejam interpretadas como incidente.

#### Scenario: Contador de tráfego na página
- **WHEN** a página inclui uma entrada de contagem de visitas junto dos serviços
- **THEN** essa entrada não participa da avaliação nem aparece no payload

### Requirement: Rule strategies
O monitor `steam` SHALL suportar avaliação por severidade estruturada, por palavra-chave e por
expressão regular sobre o corpo da página.

#### Scenario: Expressão regular inválida
- **WHEN** a expressão regular configurada não compila
- **THEN** o monitor retorna `MonitorStatus.ERROR` identificando o problema como regex inválida

#### Scenario: Estratégia desconhecida
- **WHEN** a estratégia configurada não é uma das suportadas
- **THEN** o monitor retorna `MonitorStatus.ERROR` nomeando a estratégia recebida

### Requirement: Controlled failure path
O monitor `steam` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção ao scheduler em caso de
falha de rede, resposta de erro, corpo vazio ou página que não permita extrair serviço algum,
sempre registrando o motivo e a duração da tentativa.

#### Scenario: Fonte recusa a requisição
- **WHEN** a fonte responde com erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível, e os demais monitores seguem

#### Scenario: Página sem serviços reconhecíveis
- **WHEN** a resposta chega mas nenhum serviço pode ser extraído
- **THEN** o monitor retorna `MonitorStatus.ERROR` indicando ausência de serviços na página
