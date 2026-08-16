## Context
Contrato do Alertmanager, verificado em 2026-08-16 na documentação oficial:

- `POST /api/v2/alerts`, `Content-Type: application/json`, corpo é um **array** de alertas.
- Só `labels.alertname` é obrigatório. `labels` deduplica; `annotations` carrega texto livre.
- Resolvido é sinalizado com `endsAt` no passado.
- **`endsAt` omitido** faz o Alertmanager definir `agora + resolve_timeout` (default 5 min) e
  resolver o alerta sozinho quando esse prazo vencer.
- Clientes devem **retransmitir alertas firing continuamente** até resolverem.

## O conflito, e por que ele decide o desenho
`NOTIFICATION_REPEAT_MINUTES` (default 10) existe para **não** reenviar. Com o `resolve_timeout`
default de 5 min, um alerta contínuo auto-resolveria aos 5, seria recriado aos 10, e alternaria para
sempre — flapping, com resolvido e firing revezando no plantão.

A issue registrou duas saídas. A investigação eliminou uma delas.

**O throttle está na máquina de estado, não no notifier.** `handle_result` calcula `should_send` e
**retorna antes de chamar qualquer canal**. Um canal não pode "ignorar o throttle": ele nunca é
chamado. Fazer o Alertmanager receber todo ciclo exigiria despacho diferenciado por canal, desmontando
a uniformidade que a #23 acabou de estabelecer, e reintroduzindo no manager o conhecimento de canais
específicos que ela removeu.

## Decisions

### `endsAt` explícito, com o repeat existente como heartbeat
Cada alerta firing vai com `endsAt = event_time + margem`, onde a margem é maior que o intervalo real
entre dois envios. O Alertmanager mantém o alerta firing sem precisar de retransmissão extra, e cada
reenvio do throttle estende o prazo. A recuperação manda `endsAt` no passado.

Isso não exige mudança nenhuma na máquina de estado: o `NOTIFICATION_REPEAT_MINUTES` que já existe
vira exatamente o heartbeat que o Alertmanager pede.

**A margem precisa cobrir o pior caso entre dois envios.** O reenvio acontece no primeiro ciclo de
checagem em ou após a janela vencer, então o intervalo real é `repeat + até um interval`. A margem
default é `2 × repeat + 2 × interval` — com os defaults, 22 minutos.

**O trade-off, dito às claras:** se o Service Checker morrer, os alertas ficam firing no Alertmanager
até a margem vencer, em vez de resolverem por timeout em 5 min. É deliberado — um checker morto não
deve limpar alertas em silêncio —, mas significa que o Alertmanager pode mostrar um alerta obsoleto
por até a margem. Quem quiser o comportamento oposto reduz `ALERTMANAGER_RESOLVE_AFTER_SECONDS`,
aceitando o flapping em troca.

### `alertname` fixo por tipo, identidade nas labels
Convenção do Prometheus: `alertname` é o nome da regra, e as labels distinguem as instâncias. Então
dois valores fixos — um para incidente de serviço, outro para falha de monitoramento — e a identidade
vai em `check_id`, `module` e `component`.

`alertname` variável criaria um alerta novo por incidente em vez de deduplicar, e furaria agrupamento
e silenciamento no Alertmanager.

### Texto livre em annotations, nunca em labels
`reason` e `message` vão para `annotations`. Colocá-los em label explodiria a cardinalidade — cada
texto distinto viraria uma série nova. É o mesmo raciocínio que mantém `summary` fora das labels em
qualquer regra Prometheus bem escrita.

### Labels estáticas configuráveis
Integrar com Alertmanager só vale se ele conseguir rotear. `ALERTMANAGER_EXTRA_LABELS` aceita
`env=prod,cluster=main` e as mescla nas labels de todo alerta. Labels reservadas pelo canal não podem
ser sobrescritas por essa configuração, senão a identidade do alerta viraria configurável por engano.

### `generatorURL` fica de fora
O campo é opcional e o valor natural seria a URL da página de status do provedor. O notifier não a
recebe: o contrato passa `interval_seconds`, não o `ModuleConfig`. Mudar isso alteraria a assinatura
dos quatro métodos em todos os canais, o que está fora do escopo desta change. Anotado como melhoria
futura.

## Risks / Trade-offs
- **Alerta obsoleto após queda do checker** → limitado pela margem, configurável, documentado.
- **`alertname` ou labels instáveis** furariam a deduplicação → teste de estabilidade entre ciclos,
  mesma classe de defeito da colisão de chave corrigida na #6.
- **Cardinalidade** → texto livre só em annotations, com teste garantindo que `reason` não vira label.
- **Sem teste de integração real** → o projeto não tem testcontainers; o corpo enviado é assertado
  contra o contrato documentado, e fica um smoke manual no README do canal.

## Migration Plan
Nenhuma. Canal desligado por default.
