## MODIFIED Requirements
### Requirement: Notify monitoring recovery
O sistema SHALL emitir uma notificação quando um módulo voltar a conseguir avaliar seu upstream de
forma sustentada, mas SOMENTE se a falha de monitoramento correspondente tiver sido notificada.

#### Scenario: Monitoramento restabelecido após falha notificada
- **WHEN** um módulo que teve uma falha de monitoramento notificada produz
  `NOTIFICATION_RECOVERY_THRESHOLD` resultados `OK` ou `ALERT` seguidos
- **THEN** uma notificação de monitoramento restabelecido é emitida

#### Scenario: Recuperação de falha nunca notificada
- **WHEN** um módulo acumula menos de N erros consecutivos e volta a avaliar com sucesso
- **THEN** nenhuma notificação de monitoramento restabelecido é emitida, porque nenhuma falha
  chegou a ser anunciada

### Requirement: Preserve pending alert state across monitoring failures
O sistema SHALL contabilizar erros consecutivos sem alterar o estado de alerta pendente, de modo
que a notificação de recuperação de serviço e a janela de repetição de alerta continuem se
comportando como especificado.

#### Scenario: Alerta pendente sobrevive a uma falha de monitoramento notificada
- **WHEN** um módulo entra em `ALERT`, depois acumula erros suficientes para notificar falha de
  monitoramento, e por fim retorna `OK` por `NOTIFICATION_RECOVERY_THRESHOLD` ciclos
- **THEN** tanto a notificação de monitoramento restabelecido quanto a de recuperação de serviço
  são emitidas, e a de recuperação de serviço reporta o texto do alerta original como
  `from_status`

#### Scenario: Recuperação de serviço não espera a histerese do monitor
- **WHEN** um módulo com falha de monitoramento notificada e alerta pendente produz um único `OK`
- **THEN** a recuperação de serviço é emitida nesse ciclo, porque o provedor disse que o serviço
  voltou, e a falha de monitoramento continua aberta até completar a histerese

## ADDED Requirements
### Requirement: Monitoring recovery hysteresis
O sistema SHALL exigir `NOTIFICATION_RECOVERY_THRESHOLD` avaliações bem-sucedidas seguidas
(default `3`, mínimo `1`) para encerrar uma falha de monitoramento notificada, e SHALL tratar um
`ERROR` dentro dessa janela como continuação da mesma falha. Sem isso, um upstream que oscila gera um
par falha/restabelecido a cada oscilação, e o throttle de repetição nunca chega a agir.

#### Scenario: Oscilação dentro de uma falha notificada
- **WHEN** com limiar de erro 3 e de recuperação 3, um módulo produz `E E E O E E E O O O`
- **THEN** são emitidas exatamente uma notificação de falha e uma de restabelecimento

#### Scenario: Erro dentro da janela respeita o throttle
- **WHEN** uma falha foi notificada e, depois de menos de `NOTIFICATION_RECOVERY_THRESHOLD`
  sucessos, o módulo volta a produzir `ERROR`
- **THEN** a contagem de sucessos e a de erros consecutivos voltam a zero, e uma nova notificação
  de falha só é emitida quando houver N erros seguidos e `NOTIFICATION_REPEAT_MINUTES` desde a
  última

#### Scenario: Restabelecimento recusado
- **WHEN** a janela de sucessos se completa e nenhum canal aceita a notificação de restabelecimento
- **THEN** a falha continua registrada, e o próximo sucesso tenta notificar de novo

#### Scenario: Limiar 1 preserva o comportamento anterior
- **WHEN** `NOTIFICATION_RECOVERY_THRESHOLD` é `1`
- **THEN** o primeiro sucesso depois de uma falha notificada emite o restabelecimento

#### Scenario: Janela sobrevive a restart
- **WHEN** o processo reinicia no meio da janela, com o estado persistido
- **THEN** a contagem de sucessos é restaurada, e um estado gravado sem ela carrega como zero

#### Scenario: Falha persistida fora dos limites
- **WHEN** o estado persistido traz uma falha notificada com data no futuro ou mais velha que
  `NOTIFICATION_STATE_MAX_AGE_MINUTES`
- **THEN** ela é descartada no carregamento, como um alerta nas mesmas condições

### Requirement: Blind window is logged only for an attempted notification
O sistema SHALL registrar `monitor_blind_window` somente quando uma sequência de erros atingiu o
limiar, ou seja, quando um envio de falha foi tentado e nenhum canal aceitou, e SHALL NOT registrá-lo
para uma sequência que terminou abaixo do limiar, na qual nada chegou a ser tentado.

#### Scenario: Sequência abaixo do limiar
- **WHEN** com limiar 3, um módulo produz `E E O`
- **THEN** nenhuma notificação é emitida e nenhum `monitor_blind_window` é registrado

#### Scenario: Envio tentado e recusado
- **WHEN** um módulo atinge o limiar, nenhum canal aceita a notificação de falha, e depois volta a
  avaliar com sucesso
- **THEN** `monitor_blind_window` é registrado com a contagem e o último erro
