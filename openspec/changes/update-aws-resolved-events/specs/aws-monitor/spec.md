## MODIFIED Requirements
### Requirement: Presence in the feed means the event is active
O monitor `aws` SHALL tratar a presença no feed de eventos correntes como o sinal de atividade, a
menos que o próprio evento traga um `end_time` válido, que SHALL encerrá-lo. O feed mantém por um
tempo eventos já encerrados, e só esses trazem `end_time`; tratá-los como ativos deixa o alerta
aberto e o all-clear sem sair.

O encerramento SHALL ser decidido apenas pelo `end_time`, e SHALL NOT ser inferido do texto do
resumo nem do indicador numérico de severidade.

#### Scenario: Critério de atividade
- **WHEN** um evento consta do feed sem `end_time`
- **THEN** ele é considerado ativo

#### Scenario: Evento encerrado ainda listado
- **WHEN** um evento consta do feed com `end_time` numérico não posterior ao instante da checagem
- **THEN** ele é considerado encerrado e não gera alerta

#### Scenario: Último evento das regiões monitoradas encerra
- **WHEN** o único evento das regiões filtradas passa a trazer `end_time`
- **THEN** o monitor retorna `MonitorStatus.OK`, e a recuperação do componente é notificada pelo
  caminho normal

#### Scenario: Encerramento ilegível
- **WHEN** um evento traz `end_time` que não é um epoch válido
- **THEN** ele continua considerado ativo, e o fato é registrado em log

#### Scenario: Encerramento no futuro
- **WHEN** um evento traz `end_time` posterior ao instante da checagem
- **THEN** ele continua considerado ativo

#### Scenario: Resumo marcado como resolvido sem encerramento
- **WHEN** um evento sem `end_time` traz `[RESOLVED]` no resumo
- **THEN** ele continua considerado ativo, porque o resumo é texto livre e não decide
