## ADDED Requirements

### Requirement: Deliver alerts to Alertmanager
O sistema SHALL entregar incidentes de serviço e falhas de monitoramento ao endpoint de alertas do
Alertmanager, quando o canal estiver habilitado, usando o formato de array de alertas que ele espera.

#### Scenario: Canal desabilitado
- **WHEN** o canal Alertmanager não está habilitado
- **THEN** nenhuma requisição é feita ao Alertmanager e os demais canais seguem inalterados

#### Scenario: Alerta de serviço entregue
- **WHEN** um módulo reporta um serviço degradado e o canal está habilitado
- **THEN** um alerta é enviado ao Alertmanager, com `alertname` preenchido e identidade nas labels

#### Scenario: Falha de monitoramento entregue
- **WHEN** o checker não consegue avaliar um provedor por ciclos suficientes
- **THEN** um alerta é enviado, distinguível de um incidente de serviço pelas labels

### Requirement: Represent the firing state without extra retransmission
O sistema SHALL enviar um prazo de expiração explícito em cada alerta firing, calculado de modo a
exceder o intervalo real entre dois envios consecutivos, para que o Alertmanager mantenha o alerta
ativo sem que o alerta expire entre um envio e o seguinte.

#### Scenario: Incidente que persiste por vários ciclos
- **WHEN** um incidente permanece ativo por mais tempo que a janela de repetição
- **THEN** cada reenvio estende o prazo de expiração, e o alerta permanece continuamente firing, sem
  alternar entre resolvido e firing

#### Scenario: Prazo cobre a janela de repetição
- **WHEN** a janela de repetição e o intervalo de checagem são configurados
- **THEN** o prazo de expiração default é maior que a soma deles, com folga

### Requirement: Signal resolution explicitly
O sistema SHALL sinalizar a recuperação enviando o mesmo alerta com prazo de expiração no passado, de
modo que o Alertmanager o resolva imediatamente em vez de esperar o timeout.

#### Scenario: Serviço se recupera
- **WHEN** um serviço que estava em alerta volta a operar
- **THEN** o alerta correspondente é reenviado com prazo de expiração já vencido

#### Scenario: Monitoramento se restabelece
- **WHEN** o checker volta a conseguir avaliar um provedor após uma falha notificada
- **THEN** o alerta de falha de monitoramento é reenviado com prazo de expiração já vencido

### Requirement: Keep alert identity stable and deduplicable
O sistema SHALL manter `alertname` fixo por tipo de evento e SHALL colocar a identidade do incidente
nas labels, de modo que o mesmo incidente em ciclos consecutivos produza exatamente o mesmo conjunto
de labels.

#### Scenario: Mesmo incidente em dois ciclos
- **WHEN** o mesmo incidente é reportado em dois ciclos consecutivos
- **THEN** o conjunto de labels é idêntico, permitindo que o Alertmanager deduplique

#### Scenario: Dois incidentes distintos
- **WHEN** dois componentes diferentes do mesmo provedor estão em incidente
- **THEN** os dois alertas têm conjuntos de labels distintos e não são deduplicados um no outro

### Requirement: Keep free text out of labels
O sistema SHALL colocar texto livre — motivo e mensagem — em annotations, e SHALL NOT usá-lo como
valor de label.

#### Scenario: Incidente com descrição longa
- **WHEN** um incidente carrega texto descritivo do provedor
- **THEN** esse texto aparece nas annotations e nenhuma label o contém

### Requirement: Allow static routing labels
O sistema SHALL permitir configurar labels estáticas adicionais, mescladas em todo alerta, para que o
Alertmanager consiga rotear; e SHALL NOT permitir que essa configuração sobrescreva as labels que
definem a identidade do alerta.

#### Scenario: Labels de roteamento configuradas
- **WHEN** labels estáticas são configuradas
- **THEN** elas aparecem em todo alerta enviado

#### Scenario: Tentativa de sobrescrever identidade
- **WHEN** a configuração declara uma label que o canal usa para identificar o alerta
- **THEN** o valor calculado pelo canal prevalece

### Requirement: Degrade safely when Alertmanager is unreachable
O sistema SHALL registrar falhas de entrega ao Alertmanager sem propagar exceção, preservando o
monitor e os demais canais.

#### Scenario: Alertmanager indisponível
- **WHEN** o envio falha por timeout, erro de rede ou resposta de erro
- **THEN** o ciclo de monitoramento continua e os demais canais recebem o mesmo evento normalmente
