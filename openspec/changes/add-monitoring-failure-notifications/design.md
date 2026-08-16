## Context
O `NotificationManager` mantém `_alert_state: dict[str, AlertState]` em memória, com uma entrada
por módulo (chave = slug) ou por componente (chave = `slug:component_id`). Hoje o `AlertState`
guarda `last_status`, `last_alert_at` e `last_status_text`.

Desde a v2.2.3, um resultado `ERROR` **não toca** o estado — decisão tomada para não descartar o
alerta pendente nem reiniciar a janela de repetição. Esta change reintroduz escrita de estado no
caminho de `ERROR`, mas num campo separado, preservando aquela garantia.

## Goals / Non-Goals
- Goals
  - Tornar observável, no canal de notificação, um monitor que perdeu o upstream.
  - Não gerar ruído em falha transitória.
  - Manter intactos os comportamentos de `ALERT` e `RECOVERY` corrigidos na v2.2.3.
- Non-Goals
  - Métricas Prometheus ou endpoint `/metrics` — o serviço não expõe HTTP.
  - Persistir estado entre reinícios do processo.
  - Notificação de erro por componente — inalcançável hoje (ver Impact da proposta).

## Decisions

### Contador em mapa separado, não em `AlertState`
*Decisão revista durante a implementação.* A proposta original punha `consecutive_errors` e
`last_error_notified_at` dentro do `AlertState`. Isso quebra o teste de regressão da v2.2.3
`test_error_does_not_pollute_state_of_per_service_module`, que asserta que um `ERROR` não cria
chave de nível de módulo em `_alert_state` — e, mais fundo, mistura dois espaços de chave
diferentes: `_alert_state` é indexado por módulo **e** por componente (`module:component`),
enquanto falha de monitoramento é sempre por módulo.

O `NotificationManager` ganha um segundo mapa, `_error_state: dict[str, MonitorErrorState]`, com
`consecutive_errors`, `last_notified_at` e `last_reason`. O caminho de `ERROR` mexe só nesse mapa e
nunca toca `_alert_state`, então a garantia da v2.2.3 fica preservada por construção — não por
disciplina de quem edita depois.

Um `OK` ou um `ALERT` descartam a entrada de `_error_state` do módulo.

### ERROR é interceptado antes do dispatch por serviço
`handle_result` trata `ERROR` no topo, antes de olhar o formato do payload. Falha de avaliação é
sobre o monitor, não sobre um componente, então não faz sentido rotear por componente. Efeito
colateral: o branch `!= ALERT` de `_handle_service_result` vira inalcançável e sai.

### Threshold de 3, configurável
`NOTIFICATION_ERROR_THRESHOLD`, default `3`. Com o intervalo default de 60s, são ~3 minutos de
cegueira antes de alguém ser avisado — tolerante a um timeout isolado ou 5xx transitório, sem
deixar um monitor morto passar despercebido. Valor `< 1` é elevado a `1`, como já é feito com
`repeat_minutes` em `app/core/config.py`.

### Erro sustentado respeita a mesma janela de repetição
Passado o threshold, a notificação repete no máximo a cada `NOTIFICATION_REPEAT_MINUTES`, usando
`last_error_notified_at`. Sem isso, um upstream fora do ar por um dia geraria 1440 cards.

### Um estado de erro só "recupera" se tiver sido notificado
Se o monitor errou 2 vezes (abaixo do threshold) e voltou, ninguém foi avisado do erro — então
não há o que recuperar, e nenhuma notificação de volta é emitida. A notificação de recuperação de
monitoramento só sai se `last_error_notified_at is not None`.

### Valores de status do webhook
`MONITOR_ERROR` e `MONITOR_RECOVERED`. O prefixo deixa explícito que o evento é sobre o
monitoramento, não sobre o serviço monitorado — que é exatamente a distinção que o operador de
plantão precisa fazer. Reaproveitar `RESOLVED` colapsaria "o serviço voltou" e "o monitor voltou"
num valor só.

### Cards visualmente distintos
`telegram_monitor_error.j2` usa 🛑 e o título "Monitoring failure", contra o 🚨 "ALERT" do
`telegram_alert.j2` e o ✅ "Resolved" do `telegram_resolved.j2`. O corpo diz qual módulo, há
quantos ciclos, e qual o último motivo de falha.

## Risks / Trade-offs
- **Threshold baixo demais vira ruído** → default conservador, configurável por env.
- **Reintroduzir escrita de estado no caminho de ERROR pode regredir a v2.2.3** → o campo é
  separado e os testes daquela change (`tests/test_recovery_notifications.py`) são o guarda;
  precisam continuar verdes sem alteração.
- **Estado em memória**: um restart zera `consecutive_errors`, então um monitor cronicamente
  quebrado renotifica após o restart. Aceitável — é o mesmo comportamento que alerta e recovery já
  têm hoje.

## Migration Plan
Nenhuma migração. A env nova tem default; operadores que não fizerem nada passam a receber a
notificação após 3 ciclos. Quem quiser o comportamento antigo (mudo) pode desligar apontando o
threshold para um valor alto — documentar isso no README de notificações.
