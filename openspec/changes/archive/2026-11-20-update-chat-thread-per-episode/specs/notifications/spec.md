## ADDED Requirements

### Requirement: Google Chat thread per alert episode
Com `GOOGLE_CHAT_THREAD_BY_CHECK` ligado, a chave da thread de um alerta de serviço SHALL identificar o episódio: o módulo, o componente e o início do alerta (`started_at`, em segundos Unix). O alerta, as repetições e a recuperação do mesmo episódio SHALL usar a mesma chave, inclusive depois de um restart. Um novo episódio do mesmo componente SHALL usar outra chave. Um resultado sem `started_at` SHALL usar a chave `<módulo>:<componente>`, ou `<módulo>` sem componente.

#### Scenario: Episódio inteiro
- **WHEN** `Responses` alerta às 13:01, repete às 13:11 e se recupera às 13:58
- **THEN** as três mensagens levam a mesma chave e ficam numa thread

#### Scenario: Novo episódio
- **WHEN** `Responses` se recuperou ontem e alerta de novo hoje
- **THEN** o alerta de hoje abre uma thread nova

#### Scenario: Restart
- **WHEN** o pod reinicia no meio de um episódio
- **THEN** a recuperação depois do restart responde na thread do alerta

#### Scenario: Falha de monitoramento
- **WHEN** um módulo não consegue checar
- **THEN** o aviso usa a chave do módulo, como antes
