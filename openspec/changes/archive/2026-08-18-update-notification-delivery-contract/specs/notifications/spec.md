## ADDED Requirements
### Requirement: A channel reports whether it delivered
Cada canal de notificação SHALL informar a quem despachou se a entrega foi aceita por pelo menos um
destino, e o sistema SHALL considerar o evento entregue somente quando algum canal informar aceite.

Um canal continua não propagando exceção de transporte — o isolamento entre canais depende disso —,
mas engolir a falha sem reportá-la faz o estado do alerta avançar como se alguém tivesse recebido, e
o throttle de `NOTIFICATION_REPEAT_MINUTES` suprime a repetição do que ninguém viu.

#### Scenario: Transport fails on the only channel
- **WHEN** o único canal registrado falha ao enviar, por exceção de transporte
- **THEN** o evento não é considerado entregue, o estado do alerta não avança, e o ciclo seguinte
  tenta enviar de novo

#### Scenario: Upstream rejects the request
- **WHEN** o canal recebe resposta com status maior ou igual a 400
- **THEN** o resultado é o mesmo de uma falha de transporte: não houve entrega

#### Scenario: One channel fails and another accepts
- **WHEN** dois canais estão registrados, um falha e o outro aceita
- **THEN** o evento é considerado entregue, o estado avança, e a falha do primeiro é registrada em
  log com o canal identificado

#### Scenario: Partial success across multiple targets
- **WHEN** um canal envia para vários destinos e ao menos um aceita
- **THEN** o canal informa entrega, porque alguém recebeu, e o evento não é reenviado no ciclo
  seguinte

#### Scenario: No channel registered
- **WHEN** nenhum canal está registrado
- **THEN** o estado avança como sempre avançou, porque não havia o que entregar

#### Scenario: Channel returns outside the contract
- **WHEN** um canal registrado devolve algo que não é um booleano
- **THEN** o evento não é considerado entregue por causa dele, e a violação de contrato é registrada
  em log — em vez de ser interpretada como aceite
