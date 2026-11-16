## ADDED Requirements

### Requirement: Detect a connectivity loss of the checker
O sistema SHALL tratar como queda de conectividade do próprio checker uma falha em que, no momento em
que um módulo atinge o limiar de erro, a fração dos módulos carregados em erro é igual ou maior que
`NOTIFICATION_CONNECTIVITY_QUORUM` (default `0.7`), e SHALL NOT anunciar falha de monitoramento por
módulo enquanto a queda estiver aberta. Com menos de 3 módulos carregados a detecção SHALL ficar
desligada.

#### Scenario: Maioria dos módulos falhando junta
- **WHEN** 10 módulos estão carregados e todos falham ao mesmo tempo até atingir o limiar
- **THEN** `connectivity_lost` é registrado uma vez, e nenhum `monitor_failure` por módulo é emitido

#### Scenario: Falha isolada de um provedor
- **WHEN** só um dos módulos carregados atinge o limiar de erro
- **THEN** a falha de monitoramento desse módulo é anunciada como antes desta mudança

#### Scenario: Poucos módulos
- **WHEN** menos de 3 módulos estão carregados
- **THEN** cada falha segue o fluxo por módulo, sem detecção de queda

#### Scenario: Módulo que segue em erro depois da queda
- **WHEN** a fração em erro fica abaixo do quórum e um módulo continua falhando
- **THEN** a queda termina, e esse módulo só anuncia falha depois de atingir o limiar de novo, contado
  a partir do fim da queda

### Requirement: Connectivity loss tolerance
Uma queda de conectividade SHALL notificar no máximo uma vez, como a falha de monitoramento do módulo
`checker`, e somente quando a duração atingir `NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES` (default
`10`; `0` notifica toda queda). O fim de uma queda anunciada SHALL ser anunciado como a recuperação do
`checker`, com início, fim e duração. Uma queda mais curta que a tolerância SHALL ficar só no log
(`connectivity_restored`, com a duração).

#### Scenario: Queda curta
- **WHEN** a tolerância é 10 minutos e a queda dura 6 minutos
- **THEN** nenhum canal recebe mensagem, e o log registra o início, o fim e a duração

#### Scenario: Queda longa
- **WHEN** a queda atinge a tolerância
- **THEN** uma falha de monitoramento do `checker` é enviada, e quando a queda termina, a recuperação
  dela

#### Scenario: Canal indisponível durante a queda
- **WHEN** nenhum canal aceita o aviso da queda
- **THEN** o aviso é tentado de novo no resultado seguinte, e a recuperação só é enviada depois da
  falha

### Requirement: Expected connectivity windows
Com `NOTIFICATION_CONNECTIVITY_WINDOWS` definida (por exemplo `04:00-04:15,05:00-05:10`, no fuso
`NOTIFICATION_CONNECTIVITY_TIMEZONE`, default `America/Sao_Paulo`), uma queda de conectividade que
começa dentro de uma janela e termina antes do fim dela SHALL NOT notificar, seja qual for a duração;
uma queda que passa do fim da janela SHALL seguir a tolerância, contada desde o início da queda. A
janela SHALL valer somente para a queda de conectividade.

#### Scenario: Queda inteira dentro da janela
- **WHEN** a janela é `04:00-04:15`, a tolerância é `0`, e a queda vai de 04:00 a 04:06
- **THEN** nenhum canal recebe mensagem

#### Scenario: Queda que sai da janela
- **WHEN** a janela é `04:00-04:15`, a tolerância é `0`, e a queda vai de 04:10 a 04:20
- **THEN** a falha do `checker` é anunciada depois das 04:15, e a recuperação no fim

#### Scenario: Falha isolada dentro da janela
- **WHEN** dentro da janela só um módulo atinge o limiar de erro
- **THEN** a falha desse módulo é anunciada normalmente

#### Scenario: Janela que cruza a meia-noite
- **WHEN** a janela é `23:50-00:10` e a queda vai de 23:55 a 00:05
- **THEN** nenhum canal recebe mensagem

#### Scenario: Item mal formado
- **WHEN** a variável tem um item que não é `HH:MM-HH:MM` válido
- **THEN** o item é ignorado com aviso de configuração, e os itens válidos continuam valendo

### Requirement: Connectivity loss survives a restart
O estado da queda de conectividade SHALL ser persistido junto com o estado de notificação, sem mudar a
versão do esquema, e restaurado no início do processo.

#### Scenario: Restart no meio da queda
- **WHEN** o processo reinicia com uma queda aberta no estado persistido
- **THEN** a queda continua com o mesmo início, e a tolerância conta a partir dele

## MODIFIED Requirements

### Requirement: Notify sustained monitoring failure
O sistema SHALL emitir uma notificação quando um módulo acumular N resultados `ERROR` consecutivos,
onde N é configurável por `NOTIFICATION_ERROR_THRESHOLD` (default `3`), e SHALL permanecer em
silêncio antes disso para não gerar ruído em falha transitória. Enquanto uma queda de conectividade do
checker estiver aberta, a falha do módulo SHALL NOT ser anunciada (ver *Detect a connectivity loss of
the checker*).

#### Scenario: Falha transitória abaixo do threshold
- **WHEN** um módulo produz `N - 1` resultados `ERROR` consecutivos e em seguida um `OK`
- **THEN** nenhuma notificação de falha de monitoramento é emitida

#### Scenario: Threshold atingido
- **WHEN** um módulo produz o N-ésimo resultado `ERROR` consecutivo, sem queda de conectividade aberta
- **THEN** exatamente uma notificação de falha de monitoramento é emitida, contendo o identificador
  do módulo, a contagem de ciclos consecutivos e o motivo do último erro

#### Scenario: Contador zerado por avaliação bem-sucedida
- **WHEN** um módulo produz `ERROR` algumas vezes e depois qualquer resultado `OK` ou `ALERT`
- **THEN** a contagem de erros consecutivos volta a zero, e um novo bloco de erros precisa atingir
  o threshold de novo antes de notificar

### Requirement: Blind window is logged only for an attempted notification
O sistema SHALL registrar `monitor_blind_window` somente quando uma sequência de erros atingiu o
limiar, ou seja, quando um envio de falha foi tentado e nenhum canal aceitou, e SHALL NOT registrá-lo
para uma sequência que terminou abaixo do limiar, na qual nada chegou a ser tentado, nem para um
módulo cuja falha foi absorvida por uma queda de conectividade.

#### Scenario: Sequência abaixo do limiar
- **WHEN** com limiar 3, um módulo produz `E E O`
- **THEN** nenhuma notificação é emitida e nenhum `monitor_blind_window` é registrado

#### Scenario: Envio tentado e recusado
- **WHEN** um módulo atinge o limiar, nenhum canal aceita a notificação de falha, e depois volta a
  avaliar com sucesso
- **THEN** `monitor_blind_window` é registrado com a contagem e o último erro

#### Scenario: Falha absorvida pela queda de conectividade
- **WHEN** um módulo atinge o limiar durante uma queda de conectividade e depois volta a avaliar com
  sucesso
- **THEN** nenhum `monitor_blind_window` é registrado para ele
