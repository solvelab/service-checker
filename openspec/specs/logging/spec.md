# logging Specification

## Purpose
Faz do log do container o painel do service-checker, que não tem dashboard: a rotina sem mudança fica abaixo de `INFO`, um quadro de status periódico mostra cada provedor e o último erro, e um formato legível para gente convive com o JSON para coletores.
## Requirements
### Requirement: Routine checks stay below INFO
O sistema SHALL emitir em `DEBUG` um `monitor_check` cujo status, motivo e mensagem são iguais aos da checagem anterior do mesmo módulo; quando a checagem levanta exceção, o tipo dela também entra na comparação. A primeira checagem de cada módulo e toda mudança de status, motivo, mensagem ou tipo de exceção SHALL sair no nível correspondente ao status: `INFO` para `OK`, `WARNING` para `ALERT`, `ERROR` para `ERROR`. Os demais eventos SHALL manter o nível atual.

#### Scenario: Checagem saudável repetida
- **WHEN** um módulo dá `OK` com o mesmo motivo da checagem anterior
- **THEN** o `monitor_check` sai em `DEBUG`, e com o nível padrão `INFO` não gera linha

#### Scenario: Mudança de status
- **WHEN** um módulo passa de `OK` para `ALERT`
- **THEN** o `monitor_check` sai em `WARNING`

#### Scenario: Erro diferente
- **WHEN** um módulo em `ERROR` passa a falhar com outro motivo
- **THEN** o `monitor_check` sai em `ERROR`

#### Scenario: Depuração
- **WHEN** `SERVICE_MONITOR_LOG_LEVEL` é `DEBUG`
- **THEN** toda checagem aparece no log, como antes

### Requirement: Status board in the log
O sistema SHALL emitir um evento `status_board`, em `INFO`, com uma linha por módulo carregado. Cada linha traz o status, desde quando, a última checagem e uma nota: o motivo atual quando o status não é `OK`, ou o último erro com horário quando o módulo já está saudável. O evento SHALL trazer também os canais ligados, o número de alertas de serviço abertos dos módulos carregados e o de módulos em `ERROR`. Ele SHALL sair depois da primeira rodada de checagens, depois de cada mudança de status (o primeiro resultado de um módulo conta como mudança), agrupando mudanças próximas num quadro só, e a cada `SERVICE_MONITOR_STATUS_BOARD_MINUTES`. O valor `0` SHALL desligar só a cadência periódica.

#### Scenario: Subida
- **WHEN** todo módulo entregou o primeiro resultado
- **THEN** um `status_board` sai com todos os módulos

#### Scenario: Mudanças próximas
- **WHEN** três módulos mudam de status na mesma rodada
- **THEN** sai um único `status_board` com os três novos status

#### Scenario: Erro passado não se perde
- **WHEN** um módulo falhou e já voltou a `OK`
- **THEN** a linha dele no quadro mostra o horário e o motivo do último erro

#### Scenario: Sem mudança
- **WHEN** nada muda por `SERVICE_MONITOR_STATUS_BOARD_MINUTES`
- **THEN** um `status_board` sai mesmo assim

### Requirement: Human-readable log format
`SERVICE_MONITOR_LOG_FORMAT` SHALL aceitar `json`, o default e o formato atual, ou `pretty`. Em `pretty`, cada evento SHALL ser uma linha curta de texto com horário, nível, módulo, mensagem e os campos da mesma allowlist do JSON. O `status_board` SHALL ser uma tabela alinhada que cabe em 100 colunas. Em `json`, o `status_board` SHALL ser um único objeto JSON parseável. Um valor desconhecido SHALL gerar aviso de configuração e usar `json`. Nenhuma credencial SHALL aparecer em nenhum dos dois formatos.

#### Scenario: Formato legível
- **WHEN** `SERVICE_MONITOR_LOG_FORMAT` é `pretty`
- **THEN** os eventos saem como texto, e o quadro como tabela alinhada

#### Scenario: Formato padrão
- **WHEN** a variável não está definida
- **THEN** os eventos saem em JSON como hoje, e o quadro é um evento JSON com a lista dos módulos

#### Scenario: Valor inválido
- **WHEN** `SERVICE_MONITOR_LOG_FORMAT` é `yaml`
- **THEN** a subida avisa e usa `json`

