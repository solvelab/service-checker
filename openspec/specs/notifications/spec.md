# notifications Specification

## Purpose
Garantir que quem está de plantão receba exatamente um aviso por evento que importa, e que cada aviso diga qual componente mudou e para qual estado.

Cobre dois ciclos de vida distintos e que não devem ser confundidos: o do **serviço**, que degrada e se recupera, e o do **monitoramento**, que pode perder acesso ao upstream e voltar. Um monitor incapaz de avaliar não é um serviço saudável, e nunca deve parecer um.

Também garante que uma falha de monitoramento não descarte o alerta de serviço pendente: a recuperação precisa chegar mesmo que o upstream tenha ficado inalcançável no meio do incidente.

A capacidade abrange quatro canais — Telegram, webhook genérico, Google Chat e Alertmanager — e cada um traz restrição própria: cota por espaço, credencial embutida em URL, semântica de estado com prazo de expiração. Os requisitos por canal registram essas restrições; as garantias acima valem para todos.

## Requirements
### Requirement: Recovery notifications identify the exact component transition
The system SHALL generate recovery notifications using the specific check that transitioned from a non-OK state to OK, including provider name, component name, component identifier/slug (when available), previous status, current status, and the transition timestamp extracted from the state-change event.

#### Scenario: Component recovers from non-OK to OK
- **WHEN** a provider component transitions from DOWN, DEGRADED, or UNKNOWN to OK based on a state-change event
- **THEN** the emitted notification includes provider name, component name, component slug/id (if present), previous status, current status, and the event's timestamp.

#### Scenario: Provider with multiple components
- **WHEN** a provider has multiple components and only one transitions to OK
- **THEN** the notification references that specific component (not just the provider) and uses the same identifier format that was used in the incident notification for that component.

### Requirement: Distinguish component recovery from provider overall status
The system SHALL keep component-level recovery notifications distinct from provider-level aggregates and SHALL only label a notification as provider-level “overall” when the aggregate status itself transitions.

#### Scenario: Partial provider recovery
- **WHEN** one component returns to OK while another component of the same provider remains non-OK
- **THEN** the recovery message states only that the specific component recovered and does not imply full provider restoration; any aggregate notification, if sent, is explicitly labeled “Overall”.

### Requirement: Log component transition metadata for recoveries
The system SHALL log (debug or info) the unique check identifier and the from_status → to_status values whenever a recovery notification is emitted.

#### Scenario: Logged recovery transition
- **WHEN** a recovery notification is generated for a component
- **THEN** the logs contain the component’s check_id (provider_id + component_id/slug) and the previous and current statuses to support auditing of notification content.

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

### Requirement: Throttle sustained monitoring-failure notifications
O sistema SHALL respeitar `NOTIFICATION_REPEAT_MINUTES` ao repetir a notificação de falha de
monitoramento, do mesmo modo que já faz para alertas de serviço.

#### Scenario: Erro prolongado
- **WHEN** um módulo permanece em `ERROR` por muitos ciclos após o threshold ter sido atingido
- **THEN** a notificação é repetida no máximo uma vez a cada `NOTIFICATION_REPEAT_MINUTES`, e não
  uma vez por ciclo

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
  monitoramento, e por fim retorna `OK` por `NOTIFICATION_RECOVERY_THRESHOLD` ciclos
- **THEN** tanto a notificação de monitoramento restabelecido quanto a de recuperação de serviço
  são emitidas, e a de recuperação de serviço reporta o texto do alerta original como
  `from_status`

#### Scenario: Recuperação de serviço não espera a histerese do monitor
- **WHEN** um módulo com falha de monitoramento notificada e alerta pendente produz um único `OK`
- **THEN** a recuperação de serviço é emitida nesse ciclo, porque o provedor disse que o serviço
  voltou, e a falha de monitoramento continua aberta até completar a histerese

### Requirement: Deliver notifications to a Google Chat space
O sistema SHALL entregar os quatro eventos do ciclo de vida — alerta de serviço, recuperação de
serviço, falha de monitoramento e monitoramento restabelecido — a um espaço do Google Chat, quando
o canal estiver habilitado.

#### Scenario: Canal desabilitado
- **WHEN** o canal Google Chat não está habilitado na configuração
- **THEN** nenhuma requisição é feita ao Google Chat e os demais canais seguem inalterados

#### Scenario: Alerta de serviço entregue
- **WHEN** um módulo reporta um serviço degradado e o canal está habilitado
- **THEN** uma mensagem é postada no espaço configurado, identificando o módulo e o serviço afetado

#### Scenario: Os quatro eventos chegam
- **WHEN** um módulo percorre um ciclo completo de alerta, falha de monitoramento e recuperação
- **THEN** cada um dos quatro eventos produz uma mensagem própria no espaço

### Requirement: Distinguish event types visually in Google Chat
O sistema SHALL renderizar cada tipo de evento com cabeçalho próprio, de modo que alerta de serviço,
recuperação de serviço, falha de monitoramento e monitoramento restabelecido sejam distinguíveis
sem ler o corpo da mensagem.

#### Scenario: Falha de monitoramento não se confunde com serviço fora
- **WHEN** o canal entrega uma falha de monitoramento e, noutro momento, um alerta de serviço
- **THEN** os dois cabeçalhos são diferentes, e o de falha de monitoramento deixa explícito que o
  problema é a checagem e não necessariamente o provedor

### Requirement: One entry per incident in Google Chat
O sistema SHALL renderizar um item por incidente, consumindo a lista já separada pelo monitor, e
SHALL NOT reconstruir essa separação a partir da frase única.

#### Scenario: Alerta com múltiplos incidentes
- **WHEN** um alerta carrega N incidentes
- **THEN** a mensagem apresenta N itens distintos

#### Scenario: Incidente cujo texto contém pontuação de separador
- **WHEN** o texto de um incidente contém vírgula, ponto e vírgula ou barra vertical
- **THEN** ele permanece um único item, sem ser quebrado

### Requirement: Escape message content for Google Chat cards
O sistema SHALL escapar o conteúdo dinâmico segundo as regras do widget usado, de modo que texto
vindo do provedor não altere a estrutura nem a formatação da mensagem.

#### Scenario: Conteúdo com marcação
- **WHEN** o texto de um incidente contém caracteres com significado de marcação no widget
- **THEN** eles são exibidos literalmente e a mensagem chega íntegra

### Requirement: Respect the Google Chat per-space quota
O sistema SHALL limitar o ritmo de envio ao espaço para não exceder a cota de uma requisição por
segundo, mesmo quando um único ciclo produzir muitas notificações.

#### Scenario: Muitos componentes degradados no mesmo ciclo
- **WHEN** um módulo reporta vários componentes degradados num único ciclo, gerando uma notificação
  por componente
- **THEN** os envios ao Google Chat são espaçados o suficiente para respeitar a cota do espaço

#### Scenario: Cota excedida mesmo assim
- **WHEN** o Google Chat responde indicando limite de taxa excedido
- **THEN** a resposta é registrada em log e nenhuma exceção é propagada

### Requirement: Never disclose the Google Chat webhook credential
O sistema SHALL tratar a URL do webhook como credencial e SHALL NOT incluí-la em nenhuma saída de
log, inclusive nas mensagens de erro.

#### Scenario: Falha de entrega
- **WHEN** o envio ao Google Chat falha por erro de rede ou por resposta de erro do serviço
- **THEN** o log identifica o canal e a causa sem conter a URL, a chave nem o token

### Requirement: Degrade safely when Google Chat is unreachable
O sistema SHALL registrar falhas de entrega ao Google Chat sem propagar exceção, preservando o
monitor e os demais canais.

#### Scenario: Google Chat indisponível
- **WHEN** o envio falha por timeout, erro de rede ou resposta de erro
- **THEN** o ciclo de monitoramento continua e os demais canais recebem o mesmo evento normalmente

### Requirement: Deliver alerts to Alertmanager
O sistema SHALL entregar incidentes de serviço e falhas de monitoramento ao endpoint de alertas do
Alertmanager, quando o canal estiver habilitado, usando o formato de array de alertas que ele espera.

#### Scenario: Canal desabilitado
- **WHEN** o canal Alertmanager não está habilitado
- **THEN** nenhuma requisição é feita ao Alertmanager e os demais canais seguem inalterados

#### Scenario: Alerta de serviço entregue
- **WHEN** um módulo reporta um serviço degradado e o canal está habilitado
- **THEN** um alerta é enviado ao Alertmanager, com `alertname` preenchido e identidade nas labels

#### Scenario: Falha de monitoramento entregue
- **WHEN** o checker não consegue avaliar um provedor por ciclos suficientes
- **THEN** um alerta é enviado, distinguível de um incidente de serviço pelas labels

### Requirement: Represent the firing state without extra retransmission
O sistema SHALL enviar um prazo de expiração explícito em cada alerta firing, calculado de modo a
exceder o intervalo real entre dois envios consecutivos, para que o Alertmanager mantenha o alerta
ativo sem que o alerta expire entre um envio e o seguinte.

#### Scenario: Incidente que persiste por vários ciclos
- **WHEN** um incidente permanece ativo por mais tempo que a janela de repetição
- **THEN** cada reenvio estende o prazo de expiração, e o alerta permanece continuamente firing, sem
  alternar entre resolvido e firing

#### Scenario: Prazo cobre a janela de repetição
- **WHEN** a janela de repetição e o intervalo de checagem são configurados
- **THEN** o prazo de expiração default é maior que a soma deles, com folga

### Requirement: Signal resolution explicitly
O sistema SHALL sinalizar a recuperação enviando o mesmo alerta com prazo de expiração no passado, de
modo que o Alertmanager o resolva imediatamente em vez de esperar o timeout.

#### Scenario: Serviço se recupera
- **WHEN** um serviço que estava em alerta volta a operar
- **THEN** o alerta correspondente é reenviado com prazo de expiração já vencido

#### Scenario: Monitoramento se restabelece
- **WHEN** o checker volta a conseguir avaliar um provedor após uma falha notificada
- **THEN** o alerta de falha de monitoramento é reenviado com prazo de expiração já vencido

### Requirement: Keep alert identity stable and deduplicable
O sistema SHALL manter `alertname` fixo por tipo de evento e SHALL colocar a identidade do incidente
nas labels, de modo que o mesmo incidente em ciclos consecutivos produza exatamente o mesmo conjunto
de labels.

#### Scenario: Mesmo incidente em dois ciclos
- **WHEN** o mesmo incidente é reportado em dois ciclos consecutivos
- **THEN** o conjunto de labels é idêntico, permitindo que o Alertmanager deduplique

#### Scenario: Dois incidentes distintos
- **WHEN** dois componentes diferentes do mesmo provedor estão em incidente
- **THEN** os dois alertas têm conjuntos de labels distintos e não são deduplicados um no outro

### Requirement: Keep free text out of labels
O sistema SHALL colocar texto livre — motivo e mensagem — em annotations, e SHALL NOT usá-lo como
valor de label.

#### Scenario: Incidente com descrição longa
- **WHEN** um incidente carrega texto descritivo do provedor
- **THEN** esse texto aparece nas annotations e nenhuma label o contém

### Requirement: Allow static routing labels
O sistema SHALL permitir configurar labels estáticas adicionais, mescladas em todo alerta, para que o
Alertmanager consiga rotear; e SHALL NOT permitir que essa configuração sobrescreva as labels que
definem a identidade do alerta.

#### Scenario: Labels de roteamento configuradas
- **WHEN** labels estáticas são configuradas
- **THEN** elas aparecem em todo alerta enviado

#### Scenario: Tentativa de sobrescrever identidade
- **WHEN** a configuração declara uma label que o canal usa para identificar o alerta
- **THEN** o valor calculado pelo canal prevalece

### Requirement: Degrade safely when Alertmanager is unreachable
O sistema SHALL registrar falhas de entrega ao Alertmanager sem propagar exceção, preservando o
monitor e os demais canais.

#### Scenario: Alertmanager indisponível
- **WHEN** o envio falha por timeout, erro de rede ou resposta de erro
- **THEN** o ciclo de monitoramento continua e os demais canais recebem o mesmo evento normalmente

### Requirement: Pending alerts survive a process restart
O sistema SHALL persistir os alertas pendentes e o estado de erro de monitor quando `NOTIFICATION_STATE_PATH` estiver configurado, de modo que um incidente iniciado antes de um restart ainda produza a notificação de recuperação depois dele.

Sem essa persistência o all-clear se perde em silêncio: nenhum provedor publica a mesma degradação duas vezes, então após o restart o payload saudável não representa transição alguma.

#### Scenario: Incident spans a restart
- **WHEN** um componente alerta, o processo reinicia, e em seguida o provedor volta ao normal
- **THEN** a notificação de recuperação é emitida, citando o componente

#### Scenario: No state path configured
- **WHEN** `NOTIFICATION_STATE_PATH` é vazio ou ausente
- **THEN** o estado é mantido apenas em memória e o comportamento é idêntico ao anterior a esta mudança

#### Scenario: Throttle is preserved
- **WHEN** um alerta é emitido, o processo reinicia, e o componente segue degradado dentro da janela de `NOTIFICATION_REPEAT_MINUTES`
- **THEN** nenhum alerta novo é emitido, porque o instante do último envio foi restaurado junto

#### Scenario: Component already recovered before the restart
- **WHEN** o alerta já havia sido resolvido antes do restart
- **THEN** nada é restaurado para aquele componente e nenhuma recuperação é emitida

### Requirement: Stale state is discarded rather than replayed
O sistema SHALL descartar, na subida, todo alerta persistido mais antigo que `NOTIFICATION_STATE_MAX_AGE_MINUTES`, sem emitir notificação por ele.

Uma recuperação tardia demais informa menos do que confunde: o operador receberia o "resolvido" de um incidente que já não lembra.

#### Scenario: Alert older than the limit
- **WHEN** o estado contém um alerta gravado há mais tempo que o limite configurado
- **THEN** ele é descartado na carga e nenhum all-clear é emitido para ele

#### Scenario: Limit disabled
- **WHEN** `NOTIFICATION_STATE_MAX_AGE_MINUTES` é `0`
- **THEN** nenhum alerta é descartado por idade

#### Scenario: Timestamp in the future
- **WHEN** um alerta persistido tem instante mais de cinco minutos à frente do relógio atual
- **THEN** ele é descartado, porque congelaria a janela de repetição indefinidamente

### Requirement: Persistence never prevents monitoring
O sistema SHALL tratar toda falha de leitura ou escrita do estado como registrável e não-fatal, seguindo a execução com o estado que conseguiu obter.

#### Scenario: Unreadable or corrupt state file
- **WHEN** o arquivo não existe, está vazio, contém JSON inválido, não é um objeto, ou declara versão de schema diferente
- **THEN** o processo sobe com estado vazio, registra o motivo em log, e segue monitorando

#### Scenario: State cannot be written
- **WHEN** a escrita falha por permissão, disco cheio ou caminho inválido
- **THEN** o ciclo de verificação e as notificações prosseguem normalmente, e a falha é registrada

#### Scenario: Write is interrupted
- **WHEN** a escrita é interrompida no meio
- **THEN** o arquivo anterior permanece íntegro, porque a gravação ocorre em arquivo temporário seguido de rename atômico

#### Scenario: One corrupt entry among valid ones
- **WHEN** uma entrada do arquivo é inválida e as demais são válidas
- **THEN** apenas a inválida é descartada, e as válidas são restauradas

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
para uma sequência que terminou abaixo do limiar, na qual nada chegou a ser tentado, nem para um
módulo que atingiu o limiar já dentro de uma queda de conectividade.

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

### Requirement: Telegram daily summary message
No modo `daily_summary` (`TELEGRAM_MODE`), o canal Telegram SHALL manter uma única mensagem por chat
por dia, no fuso `TELEGRAM_SUMMARY_TIMEZONE`, atualizada a cada evento, listando por serviço a
contagem de problemas do dia, os problemas abertos com a hora de início e os resolvidos com início,
fim e duração, e numa seção própria as falhas de monitoramento com o motivo.

#### Scenario: Repeated alert edits the day's message
- **WHEN** um alerta já aberto é reenviado pelo throttle de repetição
- **THEN** a mensagem do dia é editada, e nenhuma mensagem nova é enviada

#### Scenario: Recovery is recorded with its time
- **WHEN** um problema aberto é resolvido
- **THEN** a mensagem do dia é editada em silêncio e passa a mostrar o problema como resolvido, com a
  hora de início, a hora de resolução e a duração, no fuso do resumo

#### Scenario: Day rollover
- **WHEN** o primeiro evento acontece depois da meia-noite no fuso do resumo
- **THEN** uma mensagem nova é criada para o novo dia, os problemas ainda abertos são carregados para
  ela, e a mensagem do dia anterior não é mais editada

#### Scenario: Message over the Telegram limit
- **WHEN** o resumo do dia passa de 4096 caracteres
- **THEN** os resolvidos de cada serviço são compactados em contagem, e todos os problemas abertos
  continuam aparecendo

#### Scenario: Per-event mode
- **WHEN** `TELEGRAM_MODE` é `per_event`
- **THEN** cada evento envia um cartão próprio, como antes desta mudança

### Requirement: Only a new problem notifies on Telegram
No modo `daily_summary`, o canal Telegram SHALL produzir uma notificação no chat somente quando um
problema novo começa, reenviando a mensagem do dia para o fim do chat; repetições, recuperações e
restabelecimentos SHALL ser edições silenciosas.

#### Scenario: New problem moves the message down
- **WHEN** um componente que estava saudável entra em alerta, ou uma falha de monitoramento atinge o
  limiar
- **THEN** a mensagem do dia é apagada e reenviada atualizada, com notificação, e o chat continua com
  uma única mensagem do dia

#### Scenario: Several new problems in one burst
- **WHEN** vários problemas novos chegam dentro da janela de reenvio
- **THEN** só o primeiro reenvia com notificação, e os seguintes editam a mensagem já reenviada

### Requirement: Daily summary survives a restart
O estado do resumo diário (entradas do dia e `message_id` de cada chat) SHALL ser persistido junto com
o estado de notificação, sem mudar a versão do esquema, e restaurado no início do processo.

#### Scenario: Restart continues the same message
- **WHEN** o processo reinicia com o estado persistido, no mesmo dia
- **THEN** o próximo evento edita a mesma mensagem, sem criar outra

#### Scenario: Older state file
- **WHEN** o arquivo de estado foi escrito antes desta mudança e não tem o resumo
- **THEN** os alertas pendentes são restaurados normalmente, e o resumo começa vazio

### Requirement: Edit failures never silence a Telegram notification
Uma falha da Bot API ao editar ou apagar a mensagem do dia SHALL resultar em uma mensagem entregue
por outro caminho, ou em entrega não confirmada para o manager, nunca em sucesso sem mensagem.

#### Scenario: Content unchanged
- **WHEN** a edição é recusada com `message is not modified`
- **THEN** o canal informa entrega, porque a mensagem já mostra o conteúdo

#### Scenario: Message no longer editable
- **WHEN** a edição é recusada porque a mensagem não existe mais ou não pode ser editada
- **THEN** uma mensagem nova é enviada com o resumo, e o seu `message_id` passa a ser o do dia

#### Scenario: Delete fails before a resend
- **WHEN** apagar a mensagem do dia falha antes de um reenvio
- **THEN** o reenvio acontece mesmo assim, e a falha é registrada em log

#### Scenario: A failed ring is not downgraded
- **WHEN** o reenvio de um problema novo falha, e outro evento edita a mensagem antes do reenvio do
  manager
- **THEN** essa escrita reenvia com notificação, e o problema não fica marcado como notificado sem ter
  tocado

#### Scenario: A refused recovery edit is retried
- **WHEN** a edição que mostra uma recuperação é recusada por limite de taxa ou erro transitório
- **THEN** o fim do próximo ciclo de qualquer módulo reescreve a mensagem, sem esperar outro evento

#### Scenario: Every chat fails
- **WHEN** nenhum chat aceita a escrita
- **THEN** o canal informa que não entregou, e o manager tenta de novo no ciclo seguinte

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
Uma queda de conectividade SHALL ser anunciada como a falha de monitoramento do módulo `checker`, e
somente quando a duração atingir `NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES` (default `10`; `0`
notifica toda queda); enquanto aberta, SHALL ser repetida a cada `NOTIFICATION_REPEAT_MINUTES`, como
qualquer falha de monitoramento, levando o início real da queda. O fim de uma queda anunciada SHALL ser anunciado como a recuperação do
`checker`, com início, fim e duração. Uma queda mais curta que a tolerância SHALL ficar só no log
(`connectivity_restored`, com a duração).

#### Scenario: Queda curta
- **WHEN** a tolerância é 10 minutos e a queda dura 6 minutos
- **THEN** nenhum canal recebe mensagem, e o log registra o início, o fim e a duração

#### Scenario: Queda longa
- **WHEN** a queda atinge a tolerância
- **THEN** uma falha de monitoramento do `checker` é enviada, e quando a queda termina, a recuperação
  dela

#### Scenario: Resultados concorrentes
- **WHEN** vários módulos terminam o ciclo ao mesmo tempo enquanto o aviso ou a recuperação da queda
  está sendo enviado
- **THEN** o aviso e a recuperação saem uma vez cada

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

#### Scenario: Queda velha no estado
- **WHEN** a queda persistida foi vista pela última vez há mais que `NOTIFICATION_STATE_MAX_AGE_MINUTES`
- **THEN** ela é descartada na subida, sem aviso
