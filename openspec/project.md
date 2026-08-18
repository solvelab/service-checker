# Project Context

## Purpose
Daemon que vigia as páginas de status de provedores terceiros e avisa quando um deles degrada —
e quando volta. Existe para trocar a checagem manual de dez páginas por um único fluxo de alertas,
com filtro por serviço e por região, para que a equipe descubra um incidente upstream antes que o
usuário reclame.

Dez provedores hoje: Steam, OpenAI, Claude, Rockstar (que absorveu Cfx.re / FiveM / RedM), OCI,
GCP, AWS, GitHub, Bitbucket e Cloudflare.

Quatro canais de saída: Telegram, webhook genérico, Google Chat e Alertmanager. Cada um é isolado —
um canal que levanta não impede os outros de receberem o mesmo evento —, e o estado de notificação
só avança quando **algum** deles aceitou: entrega que falhou é retentada no ciclo seguinte, em vez
de o throttle suprimir um alerta que ninguém viu.

## Tech Stack
- **Python 3.11**, `asyncio`, sem framework web — o processo não expõe porta nenhuma.
- Três dependências de runtime, fixadas em `requirements.txt`: `httpx` (cliente HTTP assíncrono),
  `Jinja2` (templates dos cards do Telegram) e `curl_cffi` (impersonation de TLS, necessária para
  os endpoints atrás de Cloudflare).
- Desenvolvimento: `pytest` + `pytest-asyncio`, `ruff` 0.6.9 (versão fixada na CI).
- Empacotamento: `python:3.11-slim`, imagem publicada em `ghcr.io/solvelab/service-checker`.
- Release: `semantic-release` disparado por push em `main`.

## Project Conventions

### Code Style
- `ruff check app tests scripts` é o gate; não há formatter automático configurado.
- **Identificadores, chaves de env, nomes de campo de log e valores de enum em inglês.** A prosa
  segue o idioma de cada artefato: READMEs em inglês, commits e árvore `openspec/` em português.
- Comentário existe para explicar *por que*, não *o quê*. O padrão do repositório é registrar a
  decisão e a medição que a sustentou — ver o bloco sobre profiles de impersonation em
  `app/modules/steam/monitor.py`.
- Exceções amplas ficam marcadas com `# noqa: BLE001` e sempre viram `MonitorResult(ERROR)`,
  nunca propagam para o scheduler.

### Architecture Patterns

**Fluxo.** `env → load_app_config → load_monitors → schedule_monitors → NotificationManager → canais`

**Módulos são plugins por convenção, não por registro.** `app/core/loader.py` importa
`app.modules.<slug>.monitor` e chama `get_monitor(slug)`. Não existe classe base nem `Protocol`:
o contrato é o que o loader e o scheduler chamam. Para adicionar um provedor, crie
`app/modules/<slug>/monitor.py` expondo:

| Símbolo | Assinatura | Papel |
|---|---|---|
| `get_monitor(slug)` | `(str) -> object` | fábrica que o loader chama |
| `configure(config)` | `(ModuleConfig) -> None` | recebe a config já resolvida |
| `check(http_client, logger)` | `async (AsyncClient, Logger) -> MonitorResult` | um ciclo de avaliação |

`MonitorResult` e `MonitorStatus` vivem em `app/core/types.py`. `MonitorStatus` tem exatamente três
valores: `OK`, `ALERT` e `ERROR`. Um módulo que falha ao carregar é registrado em log e pulado —
não derruba os demais.

**Configuração é só variável de ambiente**, sem arquivo de config. `SERVICE_MONITOR_MODULES` decide
quais slugs carregar, e cada slug recebe um prefixo em maiúsculas
(`app/core/config.py`, função `_load_module_config`):

```
<SLUG>_URL  <SLUG>_INTERVAL_SECONDS  <SLUG>_TIMEOUT_SECONDS  <SLUG>_USER_AGENT
<SLUG>_RULE_KIND  <SLUG>_RULE_VALUE  <SLUG>_SERVICE_FILTER  <SLUG>_ENABLED
```

As URLs default por slug ficam em `_default_url`, no mesmo arquivo. Nem todo módulo consome todas
as chaves — `steam` e `rockstar` ignoram `RULE_KIND`, `RULE_VALUE` e `USER_AGENT`, e cada README de
módulo diz o que de fato é lido.

**Um `asyncio.Task` por módulo**, em `app/core/scheduler.py`, cada um em laço próprio com o seu
intervalo. Exceção dentro do laço é logada e o laço continua; nenhum módulo derruba outro.

**Log estruturado em JSON no stdout**, com whitelist fixa de campos extras em
`app/core/logging.py`. Campo novo no log exige entrar nessa lista, senão é descartado em silêncio.

**Estado de notificação em memória**, em `app/core/notifications.py`, em dois mapas:
- `_alert_state` — por módulo (`github`) **ou** por componente (`github:api`), dependendo de o
  payload ser `list[dict]` ou não.
- `_error_state` — sempre por módulo; conta falhas consecutivas de avaliação.

Três invariantes que já custaram defeito e têm teste travando:
1. Um `ERROR` **nunca** toca `_alert_state`. Se tocar, o alerta pendente some e a notificação de
   recuperação nunca é enviada.
2. A chave de estado de um componente precisa ser **estável entre ciclos** e **distinta entre
   componentes**. Instável reenvia alerta a cada ciclo; repetida engole o alerta do vizinho.
3. `ALERT` é "o serviço caiu"; `ERROR` é "não consegui checar". São eventos diferentes, com cards
   e valores de `status` de webhook diferentes.

**O monitor entrega os itens do alerta já separados**, em `MonitorResult.reason_items`. O notifier
não re-parseia a string `reason` — não existe separador seguro, porque `,`, `;` e `|` aparecem
dentro do conteúdo de algum provedor.

### Testing Strategy
- `pytest tests/` — 203 testes hoje, nenhum toca a rede.
- Fixtures são **payloads reais capturados**, em `tests/fixtures/<slug>/`, não JSON inventado. Um
  parser validado contra dado imaginário passa pelo motivo errado.
- Nome de teste descreve o comportamento, não o método:
  `test_error_between_alert_and_ok_still_emits_recovery`.
- **Todo teste de correção de bug precisa falhar contra o código antigo.** O padrão é reverter o
  arquivo, rodar a suíte, registrar quais testes caem, e restaurar. Um teste que passa dos dois
  lados é guarda de regressão — legítimo, mas precisa ser identificado como tal.
- Fixture congelada prova o parser do dia da captura, não que o provedor não mudou desde então.
  Para isso existe:

  ```bash
  python scripts/simulate_endpoints.py .env.example
  ```

  Roda um ciclo real por módulo e confere se os campos de que cada módulo depende ainda existem no
  upstream. Um provedor renomeou campos e um módulo ficou cego por muito tempo respondendo `OK`
  com convicção; esse script é o detector daquela classe de falha.

### Git Workflow
- Trabalho começa como issue no GitHub Project; branch é `backlog/<numero>-<slug>`.
- Nunca commitar direto em `main`.
- **Conventional Commits com gitmoji na frente**: `✨ feat(escopo): descrição`. O
  `headerPattern` custom em `.releaserc.json` aceita o emoji antes do tipo; sem ele o
  `semantic-release` não parseia o commit e a versão não sobe.
- Assunto e corpo em português; nomes de código em inglês.
- Todo pull request para `main` roda `Lint` e `Run Tests`. Corte de versão e publicação da imagem
  só acontecem no push pós-merge — um PR nunca cria tag nem publica no GHCR.
- Merge por rebase; o histórico de `main` é linear.

## Domain Context
- **Módulo / slug** — um provedor monitorado. O slug é a chave de tudo: nome do diretório, prefixo
  das env vars, `module_id` no log e no payload do webhook.
- **Componente** — uma parte de um provedor com estado próprio (`Steam Store`, `GitHub Actions`).
  Provedores no formato Statuspage entregam componentes; outros só entregam um estado agregado.
  Isso decide se o ciclo de alerta é por componente ou por módulo.
- **Regra** — como o módulo decide que há incidente. `status` compara estado estruturado;
  `keyword` e `regex` varrem o corpo cru. Nem todo módulo implementa os três.
- **Ciclo de vida de um alerta** — `ALERT` quando degrada, repetido no máximo a cada
  `NOTIFICATION_REPEAT_MINUTES`; `RESOLVED` na volta para `OK`. Falha de monitoramento tem ciclo
  próprio, disparado por `NOTIFICATION_ERROR_THRESHOLD` falhas consecutivas.

## Important Constraints
- **Estado persistido, quando há onde.** `NOTIFICATION_STATE_PATH` aponta para um arquivo JSON com
  os alertas pendentes, relido na subida; vazio significa só em memória, que é o comportamento
  antigo. Sem persistência, um incidente que atravessa um restart nunca recebe o all-clear — nenhum
  provedor anuncia a mesma degradação duas vezes. O arquivo precisa sobreviver ao pod: `emptyDir`
  não serve. Estado mais velho que `NOTIFICATION_STATE_MAX_AGE_MINUTES` é descartado na leitura, em
  vez de virar uma resolução tardia e confusa.
- **Sem servidor HTTP.** Não há endpoint de health nem `/metrics`. A observabilidade é o stdout em
  JSON, e é por isso que falha de monitoramento precisa virar notificação.
- **Três dependências de runtime.** Adicionar uma quarta é decisão consciente, não conveniência.
- **Endpoints públicos, sem contrato.** Nenhum dos dez provedores garante o formato que
  publicamos consumir; alguns nem documentam a semântica dos campos. Quando a semântica não é
  documentada, o campo entra no payload como metadado e **não** decide alerta.
- **Cloudflare bloqueia por TLS fingerprint** em `steamstat.us` e na Rockstar. Trocar `User-Agent`
  não resolve; é `curl_cffi` com profile concreto e fixado. Profiles envelhecem — os módulos
  registram no README o profile validado e a data.
- **A borda recusa mesmo com o fingerprint certo.** Medido em ~30% no `steam`, e 83% dessas recusas
  passam na tentativa seguinte. Por isso a busca com impersonação repete o que é transitório (403,
  408, 429, 5xx e erro de rede) e **não** repete o que é permanente (404, corpo vazio). Cada
  tentativa extra é registrada: retry calado esconderia a degradação que ele atravessa.
- **Fallback sem sinal é o modo de falha desta base.** Canais desligados por meses, all-clear que
  nunca saía, payload malformado capturado sem virar `MonitorStatus.ERROR` — todos encontrados por
  acaso. Todo caminho de degradação precisa devolver `ERROR`, registrar em log pela árvore
  `service_monitor`, ou re-levantar. `tests/test_silent_fallbacks.py` falha quando aparece um que
  não faz nenhuma das três.

## External Dependencies
| Provedor | Fonte | Formato |
|---|---|---|
| Steam | `steamstat.us` | HTML, atrás de Cloudflare |
| Rockstar | `support.rockstargames.com/servicestatus` | HTML, atrás de WAF |
| OpenAI, Claude, GitHub, Bitbucket | `/api/v2/summary.json` | Statuspage v2 |
| OCI | `incident-summary.rss` | RSS |
| GCP | `incidents.json` | JSON |
| AWS | `health.aws.amazon.com/public/currentevents` | JSON |

Saídas: API do Telegram (`sendMessage`, `parse_mode=HTML`) e um webhook genérico por POST JSON.
O campo `status` do webhook é um conjunto **aberto** — hoje `ALERT`, `RESOLVED`, `MONITOR_ERROR` e
`MONITOR_RECOVERED`. Consumidor que levante exceção em valor desconhecido quebra num upgrade.
