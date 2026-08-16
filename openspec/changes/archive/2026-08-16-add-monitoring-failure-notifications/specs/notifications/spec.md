## ADDED Requirements

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
