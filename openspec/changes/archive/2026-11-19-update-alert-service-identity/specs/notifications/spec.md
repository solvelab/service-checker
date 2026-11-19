## MODIFIED Requirements

### Requirement: Page incidents on component alerts
Os módulos `openai`, `claude`, `github`, `bitbucket`, `cloudflare` e `retell` SHALL anexar a cada componente em alerta os títulos, até três, dos incidentes abertos da página que citam aquele componente, ou de todos os incidentes abertos quando eles não listam componentes. A fonte SHALL ser `incidents/unresolved.json`, com recuo para `incidents.json` filtrado por status aberto quando a página responder 404. Uma falha nessa busca SHALL NOT mudar o status nem o motivo do resultado. Todos os títulos SHALL ir em `incidents`; os de incidentes que não listam componente SHALL ser repetidos em `page_incidents`. Nenhum dos dois SHALL ser guardado no estado do alerta, para uma recuperação posterior não citar incidente antigo.

#### Scenario: Incidente sem componentes
- **WHEN** a OpenAI está com `Responses` em `partial_outage` e um incidente aberto "Elevated error rates for gpt-6 Luna API requests" sem componentes
- **THEN** o item `Responses` traz esse título em `incidents` e em `page_incidents`

#### Scenario: Página fora
- **WHEN** a busca de incidentes falha
- **THEN** o alerta sai igual, sem `incidents`

### Requirement: Readable Google Chat card
O cartão do Google Chat SHALL ter como título o provedor, o componente e o estado em texto legível; SHALL mostrar os incidentes da página quando houver, no lugar da mensagem fixa, e o incidente que não lista componente SHALL ter o rótulo `Open page incident (no component listed)`, distinto do incidente que cita o componente; SHALL NOT repetir o provedor no título quando o componente tem o mesmo nome; SHALL trazer um link para a página de status; SHALL escrever toda hora no fuso `GOOGLE_CHAT_TIMEZONE` (default `America/Sao_Paulo`), sem microssegundos e com o deslocamento UTC. SHALL manter as linhas do próprio monitor (região, resumo, texto de status). A repetição SHALL dizer há quanto tempo o alerta está aberto e, num alerta de componente cujo estado mudou, de qual para qual. A recuperação SHALL dizer a duração total com início e fim. A conversa por componente SHALL continuar.

#### Scenario: Recuperação
- **WHEN** `Responses` alertou às 13:01 (UTC-3) e se recuperou às 13:58
- **THEN** o cartão diz `Lasted: 57m (13:01 → 13:58)`

#### Scenario: Relógio voltou
- **WHEN** o início registrado é posterior ao instante da recuperação
- **THEN** o cartão omite a duração e o webhook nunca manda `duration_seconds` negativo

#### Scenario: Fuso inválido
- **WHEN** `GOOGLE_CHAT_TIMEZONE` não é um fuso IANA
- **THEN** o boot avisa e usa `America/Sao_Paulo`
