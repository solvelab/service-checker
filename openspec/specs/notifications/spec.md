# notifications Specification

## Purpose
Garantir que quem está de plantão receba exatamente um aviso por evento que importa, e que cada aviso diga qual componente mudou e para qual estado.

Cobre dois ciclos de vida distintos e que não devem ser confundidos: o do **serviço**, que degrada e se recupera, e o do **monitoramento**, que pode perder acesso ao upstream e voltar. Um monitor incapaz de avaliar não é um serviço saudável, e nunca deve parecer um.

Também garante que uma falha de monitoramento não descarte o alerta de serviço pendente: a recuperação precisa chegar mesmo que o upstream tenha ficado inalcançável no meio do incidente.

## Requirements
### Requirement: Recovery notifications identify the exact component transition
The system SHALL generate recovery notifications using the specific check that transitioned from a non-OK state to OK, including provider name, component name, component identifier/slug (when available), previous status, current status, and the transition timestamp extracted from the state-change event.

#### Scenario: Component recovers from non-OK to OK
- **WHEN** a provider component transitions from DOWN, DEGRADED, or UNKNOWN to OK based on a state-change event
- **THEN** the emitted notification includes provider name, component name, component slug/id (if present), previous status, current status, and the event's timestamp.

#### Scenario: Provider with multiple components
- **WHEN** a provider has multiple components and only one transitions to OK
- **THEN** the notification references that specific component (not just the provider) and uses the same identifier format that was used in the incident notification for that component.

### Requirement: Distinguish component recovery from provider overall status
The system SHALL keep component-level recovery notifications distinct from provider-level aggregates and SHALL only label a notification as provider-level “overall” when the aggregate status itself transitions.

#### Scenario: Partial provider recovery
- **WHEN** one component returns to OK while another component of the same provider remains non-OK
- **THEN** the recovery message states only that the specific component recovered and does not imply full provider restoration; any aggregate notification, if sent, is explicitly labeled “Overall”.

### Requirement: Log component transition metadata for recoveries
The system SHALL log (debug or info) the unique check identifier and the from_status → to_status values whenever a recovery notification is emitted.

#### Scenario: Logged recovery transition
- **WHEN** a recovery notification is generated for a component
- **THEN** the logs contain the component’s check_id (provider_id + component_id/slug) and the previous and current statuses to support auditing of notification content.

### Requirement: Notify sustained monitoring failure
O sistema SHALL emitir uma notificação quando um módulo acumular N resultados `ERROR` consecutivos,
onde N é configurável por `NOTIFICATION_ERROR_THRESHOLD` (default `3`), e SHALL permanecer em
silêncio antes disso para não gerar ruído em falha transitória.

#### Scenario: Falha transitória abaixo do threshold
- **WHEN** um módulo produz `N - 1` resultados `ERROR` consecutivos e em seguida um `OK`
- **THEN** nenhuma notificação de falha de monitoramento é emitida

#### Scenario: Threshold atingido
- **WHEN** um módulo produz o N-ésimo resultado `ERROR` consecutivo
- **THEN** exatamente uma notificação de falha de monitoramento é emitida, contendo o identificador
  do módulo, a contagem de ciclos consecutivos e o motivo do último erro

#### Scenario: Contador zerado por avaliação bem-sucedida
- **WHEN** um módulo produz `ERROR` algumas vezes e depois qualquer resultado `OK` ou `ALERT`
- **THEN** a contagem de erros consecutivos volta a zero, e um novo bloco de erros precisa atingir
  o threshold de novo antes de notificar

### Requirement: Throttle sustained monitoring-failure notifications
O sistema SHALL respeitar `NOTIFICATION_REPEAT_MINUTES` ao repetir a notificação de falha de
monitoramento, do mesmo modo que já faz para alertas de serviço.

#### Scenario: Erro prolongado
- **WHEN** um módulo permanece em `ERROR` por muitos ciclos após o threshold ter sido atingido
- **THEN** a notificação é repetida no máximo uma vez a cada `NOTIFICATION_REPEAT_MINUTES`, e não
  uma vez por ciclo

### Requirement: Notify monitoring recovery
O sistema SHALL emitir uma notificação quando um módulo voltar a conseguir avaliar seu upstream,
mas SOMENTE se a falha de monitoramento correspondente tiver sido notificada.

#### Scenario: Monitoramento restabelecido após falha notificada
- **WHEN** um módulo que teve uma falha de monitoramento notificada produz um resultado `OK` ou
  `ALERT`
- **THEN** uma notificação de monitoramento restabelecido é emitida

#### Scenario: Recuperação de falha nunca notificada
- **WHEN** um módulo acumula menos de N erros consecutivos e volta a avaliar com sucesso
- **THEN** nenhuma notificação de monitoramento restabelecido é emitida, porque nenhuma falha
  chegou a ser anunciada

### Requirement: Distinguish monitoring failure from service degradation
O sistema SHALL manter a notificação de falha de monitoramento visualmente e semanticamente
distinta da notificação de serviço degradado, em todos os canais.

#### Scenario: Card do Telegram
- **WHEN** uma notificação de falha de monitoramento é renderizada para o Telegram
- **THEN** ela usa um template próprio, com título e ícone diferentes dos usados por alerta de
  serviço e por recuperação de serviço

#### Scenario: Payload do webhook
- **WHEN** uma notificação de falha ou de restabelecimento de monitoramento é enviada ao webhook
- **THEN** o campo `status` do payload é `MONITOR_ERROR` ou `MONITOR_RECOVERED` respectivamente,
  nunca `ALERT` nem `RESOLVED`

### Requirement: Preserve pending alert state across monitoring failures
O sistema SHALL contabilizar erros consecutivos sem alterar o estado de alerta pendente, de modo
que a notificação de recuperação de serviço e a janela de repetição de alerta continuem se
comportando como especificado.

#### Scenario: Alerta pendente sobrevive a uma falha de monitoramento notificada
- **WHEN** um módulo entra em `ALERT`, depois acumula erros suficientes para notificar falha de
  monitoramento, e por fim retorna `OK`
- **THEN** tanto a notificação de monitoramento restabelecido quanto a de recuperação de serviço
  são emitidas, e a de recuperação de serviço reporta o texto do alerta original como
  `from_status`

