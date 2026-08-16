## ADDED Requirements

### Requirement: Deliver notifications to a Google Chat space
O sistema SHALL entregar os quatro eventos do ciclo de vida — alerta de serviço, recuperação de
serviço, falha de monitoramento e monitoramento restabelecido — a um espaço do Google Chat, quando
o canal estiver habilitado.

#### Scenario: Canal desabilitado
- **WHEN** o canal Google Chat não está habilitado na configuração
- **THEN** nenhuma requisição é feita ao Google Chat e os demais canais seguem inalterados

#### Scenario: Alerta de serviço entregue
- **WHEN** um módulo reporta um serviço degradado e o canal está habilitado
- **THEN** uma mensagem é postada no espaço configurado, identificando o módulo e o serviço afetado

#### Scenario: Os quatro eventos chegam
- **WHEN** um módulo percorre um ciclo completo de alerta, falha de monitoramento e recuperação
- **THEN** cada um dos quatro eventos produz uma mensagem própria no espaço

### Requirement: Distinguish event types visually in Google Chat
O sistema SHALL renderizar cada tipo de evento com cabeçalho próprio, de modo que alerta de serviço,
recuperação de serviço, falha de monitoramento e monitoramento restabelecido sejam distinguíveis
sem ler o corpo da mensagem.

#### Scenario: Falha de monitoramento não se confunde com serviço fora
- **WHEN** o canal entrega uma falha de monitoramento e, noutro momento, um alerta de serviço
- **THEN** os dois cabeçalhos são diferentes, e o de falha de monitoramento deixa explícito que o
  problema é a checagem e não necessariamente o provedor

### Requirement: One entry per incident in Google Chat
O sistema SHALL renderizar um item por incidente, consumindo a lista já separada pelo monitor, e
SHALL NOT reconstruir essa separação a partir da frase única.

#### Scenario: Alerta com múltiplos incidentes
- **WHEN** um alerta carrega N incidentes
- **THEN** a mensagem apresenta N itens distintos

#### Scenario: Incidente cujo texto contém pontuação de separador
- **WHEN** o texto de um incidente contém vírgula, ponto e vírgula ou barra vertical
- **THEN** ele permanece um único item, sem ser quebrado

### Requirement: Escape message content for Google Chat cards
O sistema SHALL escapar o conteúdo dinâmico segundo as regras do widget usado, de modo que texto
vindo do provedor não altere a estrutura nem a formatação da mensagem.

#### Scenario: Conteúdo com marcação
- **WHEN** o texto de um incidente contém caracteres com significado de marcação no widget
- **THEN** eles são exibidos literalmente e a mensagem chega íntegra

### Requirement: Respect the Google Chat per-space quota
O sistema SHALL limitar o ritmo de envio ao espaço para não exceder a cota de uma requisição por
segundo, mesmo quando um único ciclo produzir muitas notificações.

#### Scenario: Muitos componentes degradados no mesmo ciclo
- **WHEN** um módulo reporta vários componentes degradados num único ciclo, gerando uma notificação
  por componente
- **THEN** os envios ao Google Chat são espaçados o suficiente para respeitar a cota do espaço

#### Scenario: Cota excedida mesmo assim
- **WHEN** o Google Chat responde indicando limite de taxa excedido
- **THEN** a resposta é registrada em log e nenhuma exceção é propagada

### Requirement: Never disclose the Google Chat webhook credential
O sistema SHALL tratar a URL do webhook como credencial e SHALL NOT incluí-la em nenhuma saída de
log, inclusive nas mensagens de erro.

#### Scenario: Falha de entrega
- **WHEN** o envio ao Google Chat falha por erro de rede ou por resposta de erro do serviço
- **THEN** o log identifica o canal e a causa sem conter a URL, a chave nem o token

### Requirement: Degrade safely when Google Chat is unreachable
O sistema SHALL registrar falhas de entrega ao Google Chat sem propagar exceção, preservando o
monitor e os demais canais.

#### Scenario: Google Chat indisponível
- **WHEN** o envio falha por timeout, erro de rede ou resposta de erro
- **THEN** o ciclo de monitoramento continua e os demais canais recebem o mesmo evento normalmente
