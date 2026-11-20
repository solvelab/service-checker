## ADDED Requirements

### Requirement: Challenge tolerance
Um resultado `ERROR` cuja causa é o desafio anti-bot da borda (`UpstreamChallenged`) SHALL ser
marcado pelo módulo como desafio. Uma sequência de erros de um módulo formada somente por resultados
marcados SHALL NOT anunciar a falha de monitoramento enquanto a duração, contada desde o primeiro erro
da sequência, for menor que `NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES` (default `60`; `0` desliga a
tolerância). Com a tolerância ligada, um erro não marcado que chega a uma sequência só de desafio
ainda retida pela tolerância SHALL encerrar a parte de desafio e começar uma sequência nova nesse erro, com
contagem `1`, anunciada no limiar normal; a contagem dos desafios SHALL NOT ser herdada. A tolerância
SHALL valer somente para uma falha ainda não anunciada.

#### Scenario: Desafio intermitente abaixo da tolerância
- **WHEN** a tolerância é 60 minutos e o `steam` produz uma sequência de 45 minutos só de desafio,
  terminando num `OK`
- **THEN** nenhuma falha de monitoramento é emitida, e o log registra `monitor_challenge_absorbed`
  com o início, o fim e a contagem

#### Scenario: Desafio que passa da tolerância
- **WHEN** a sequência só de desafio atinge 60 minutos
- **THEN** a falha de monitoramento é emitida, com o motivo do desafio e o início real da sequência

#### Scenario: Sequência mista
- **WHEN** com limiar 3, uma sequência só de desafio ainda não anunciada recebe um erro que não é
  desafio
- **THEN** começa uma sequência nova nesse erro, a falha é anunciada no terceiro erro dela e não
  antes, e uma parte de desafio retida pela tolerância é registrada como `monitor_challenge_absorbed`

#### Scenario: Queda de conectividade no meio de um desafio
- **WHEN** o `steam` está numa sequência só de desafio retida e a queda do roteador começa, com o
  primeiro erro de rede do `steam` chegando antes de a queda de conectividade abrir
- **THEN** a falha do `steam` não é anunciada, e a queda de conectividade a absorve como a de qualquer
  outro módulo

#### Scenario: Falha já anunciada
- **WHEN** uma sequência só de desafio já anunciada, ou que já passou da tolerância com o envio
  recusado, recebe um erro que não é desafio
- **THEN** a sequência continua, sem reiniciar a contagem nem o início, e uma recuperação depois de
  um envio recusado registra `monitor_blind_window`

#### Scenario: Tolerância desligada
- **WHEN** a tolerância é `0`
- **THEN** a sequência só de desafio é anunciada ao atingir o limiar, como antes desta mudança, e um erro que não é
  desafio não reinicia a sequência

#### Scenario: Valor inválido
- **WHEN** a variável não é um inteiro maior ou igual a zero
- **THEN** o carregamento avisa em `config_fallback` e usa `60`

#### Scenario: Restart no meio da sequência
- **WHEN** o processo reinicia durante uma sequência só de desafio
- **THEN** a sequência continua com o mesmo início, e a tolerância conta a partir dele

#### Scenario: Desafio e queda de conectividade
- **WHEN** um módulo está numa sequência só de desafio e uma queda de conectividade abre e fecha
- **THEN** o módulo não conta para o quórum da queda, e a tolerância dele segue contando desde o
  início real da sequência

#### Scenario: Início no futuro
- **WHEN** o início restaurado da sequência está no futuro
- **THEN** a falha é anunciada no limiar, sem tolerância

## MODIFIED Requirements

### Requirement: Notify sustained monitoring failure
O sistema SHALL emitir uma notificação quando um módulo acumular N resultados `ERROR` consecutivos,
onde N é configurável por `NOTIFICATION_ERROR_THRESHOLD` (default `3`), e SHALL permanecer em
silêncio antes disso para não gerar ruído em falha transitória. Enquanto uma queda de conectividade do
checker estiver aberta, a falha do módulo SHALL NOT ser anunciada (ver *Detect a connectivity loss of
the checker*), nem enquanto uma sequência só de desafio estiver abaixo da tolerância (ver *Challenge
tolerance*).

#### Scenario: Falha transitória abaixo do threshold
- **WHEN** um módulo produz `N - 1` resultados `ERROR` consecutivos e em seguida um `OK`
- **THEN** nenhuma notificação de falha de monitoramento é emitida

#### Scenario: Threshold atingido
- **WHEN** um módulo produz o N-ésimo resultado `ERROR` consecutivo, sem queda de conectividade aberta
  e sem que a sequência seja só de desafio abaixo da tolerância
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
módulo que atingiu o limiar já dentro de uma queda de conectividade, nem para uma sequência só de
desafio que terminou abaixo da tolerância.

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

#### Scenario: Sequência absorvida pela tolerância de desafio
- **WHEN** uma sequência só de desafio passa do limiar, termina abaixo da tolerância e o módulo volta
  a avaliar com sucesso
- **THEN** nenhum `monitor_blind_window` é registrado, e sim `monitor_challenge_absorbed`
