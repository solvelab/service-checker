## MODIFIED Requirements
### Requirement: Controlled failure path
O monitor `steam` SHALL retornar `MonitorStatus.ERROR` sem propagar exceção ao scheduler em caso de
falha de rede, resposta de erro, corpo vazio ou página que não permita extrair serviço algum,
sempre registrando o motivo e a duração da tentativa. Quando a resposta de erro for um desafio
anti-bot da borda, o motivo SHALL nomear o desafio, porque a causa é a política da fonte e não uma
falha do checker, da rede ou do provedor.

#### Scenario: Fonte recusa a requisição
- **WHEN** a fonte responde com erro
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo legível, e os demais monitores seguem

#### Scenario: Borda desafia o cliente automatizado
- **WHEN** a resposta de erro traz o cabeçalho `cf-mitigated: challenge`
- **THEN** o monitor retorna `MonitorStatus.ERROR` com motivo que nomeia o desafio do Cloudflare e o
  status HTTP, e esse motivo chega ao "last error" da notificação de falha de monitoramento

#### Scenario: Recusa sem desafio
- **WHEN** a resposta de erro não traz `cf-mitigated: challenge`
- **THEN** o motivo continua sendo o status HTTP, sem afirmar desafio

#### Scenario: Página sem serviços reconhecíveis
- **WHEN** a resposta chega mas nenhum serviço pode ser extraído
- **THEN** o monitor retorna `MonitorStatus.ERROR` indicando ausência de serviços na página
