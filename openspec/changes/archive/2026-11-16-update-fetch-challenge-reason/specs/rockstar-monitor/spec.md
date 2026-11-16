## MODIFIED Requirements
### Requirement: Controlled Failure Path
O monitor `rockstar` SHALL retornar `MonitorStatus.ERROR` (sem propagar exceções para o scheduler) em casos de falha de rede, timeout, status HTTP ≥ 400 ou payload inválido/inesperado, registrando `reason` legível e `duration_ms`. Quando o status HTTP ≥ 400 for um desafio anti-bot da borda, o `reason` SHALL nomear o desafio.

#### Scenario: Upstream timeout
- **WHEN** a requisição ao endpoint excede `timeout_seconds`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` mencionando timeout e o scheduler segue executando os demais monitores

#### Scenario: Malformed payload
- **WHEN** a resposta retorna 200 mas o corpo não corresponde ao schema esperado (ex.: campo de serviços ausente)
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` indicando parse failure

#### Scenario: Edge challenges the automated client
- **WHEN** a resposta ≥ 400 traz o cabeçalho `cf-mitigated: challenge`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com `reason` que nomeia o desafio do Cloudflare e o status HTTP
