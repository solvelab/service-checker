<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

## Como este repositório se verifica

Três coisas rodam sozinhas e uma depende de decisão humana. Nesta ordem:

| O quê | Onde | Bloqueia? |
|---|---|---|
| Lint e testes em todo PR | job `Lint` e `Run Tests` | sim, é o gate |
| Aviso de change concluída sem arquivar | job `Lint` | não, só avisa |
| Simulações de endpoint e de notificação | manual, antes do PR | não, são diagnóstico |
| Checks obrigatórios para mergear | não configurado | ver a última seção |

## O fallback funciona, o sinal não

Este é o modo de falha que mais custou caro aqui, e ele não se anuncia. O caminho de degradação
funciona — o pod fica `Running`, o log fica verde — e nada diz que ele foi tomado.

Já aconteceu com: canais de notificação desligados por quatro meses; `aws` e `gcp` alertando sem
nunca mandar o all-clear; um slug removido do código apodrecendo no manifesto enquanto o loader
logava e seguia; payload malformado capturado pelo scheduler sem virar `MonitorStatus.ERROR`, o que
impedia a notificação de monitor morto; a chave `target` descartada do log, deixando
`notification channel failed` sem dizer qual canal; o `StateStore` logando numa árvore sem handler;
um rename que faria o release parar de atualizar a tag imprimindo "not found, skipping".

Todos foram encontrados por acaso. Nenhum foi encontrado procurando.

**Ao escrever um caminho de degradação, escreva o sinal junto.** Sinal aqui é uma destas três
coisas, nesta ordem de preferência:

1. devolver `MonitorStatus.ERROR` com um `reason` legível — é o contrato dos módulos, e o que faz a
   `NotificationManager` contar a falha e avisar sobre monitor morto;
2. registrar em log pelo logger da aplicação — `service_monitor` ou um filho dele. Um
   `getLogger(__name__)` fora dessa árvore **não tem handler**, porque `configure_logging` monta só
   ela e com `propagate = False`;
3. re-levantar, quando quem chama tem contexto melhor para registrar.

Chave de `extra` que não está em `_EXTRA_KEYS` some do JSON. Acrescente lá ao criar uma.

`tests/test_silent_fallbacks.py::test_no_new_silent_fallback` varre `app/` e falha quando aparece um
`except` que não faz nenhuma das três. Há uma allowlist para os casos legitimamente calados, e cada
entrada carrega o motivo por escrito — allowlist sem motivo vira carimbo. O arquivo também tem um
guarda do guarda: uma regex quebrada tornaria a varredura verde por vacuidade, e isso já aconteceu
duas vezes neste repositório.

Silêncio não é só ausência de log. `strftime` com uma diretiva inválida não levanta no Linux: devolve
o texto literal, e o alerta sai com lixo dentro. Quando o fallback depende de uma exceção que talvez
nunca ocorra, valide o resultado em vez de confiar no `except`.

## Onde o manifesto de produção vive

Não é neste repositório. O deploy do cluster é `didevlab/housek8s`, em
`02-k8s/app/service-checker/01_deployment.yaml`, sincronizado pelo ArgoCD com `selfHeal`.

`deployment.example.yaml`, aqui na raiz, é **exemplo**: não é aplicado em lugar nenhum. Uma tarefa
de fiação que só o atualiza não chega ao cluster — foi assim que o módulo `bitbucket` entrou no
código e ficou meses fora de produção, e quase aconteceu de novo com o `cloudflare`.

Ao adicionar um módulo ou uma variável, os lugares que importam são: `.env.example`, os dois
compose, o `deployment.example.yaml` **e** um item de backlog no `housek8s`.

## Verificação automática

Todo pull request para `main` executa `Lint` (`ruff check app tests scripts`) e `Run Tests`
(`pytest tests/ -v`) via `.github/workflows/ci.yml`. Corte de versão e publicação da imagem só
acontecem no push para `main`, depois do merge — um PR nunca cria tag nem publica no GHCR.

Antes de abrir o PR, rode os dois localmente com Python 3.11 e as dependências fixadas em
`requirements.txt`. Para exercitar os endpoints reais além das fixtures:

```bash
python scripts/simulate_endpoints.py .env.example
```

Esse script consulta **dez provedores reais**, então é diagnóstico e não gate determinístico.
Um módulo que falha é reexecutado antes de o script declarar falha: falha que não se repete sai
como `TRANSIENT` e não derruba a execução, falha que se repete sai não-zero. `SIMULATE_ATTEMPTS=1`
desliga a repetição.

Não coloque esse script na CI esperando sinal estável — um provedor instável por alguns minutos
ainda vai deixar a execução vermelha. Para verificar a entrega de notificação, que não toca a rede
e é determinística:

```bash
python scripts/simulate_notifications.py
```

Alcançar o provedor não é o mesmo que alertar sobre ele. O módulo `aws` lia quatro campos que o
feed nunca publicou: reportava `OK` com três incidentes ativos e passaria nos dois scripts acima.
Para provar que a degradação vira alerta entregue, e a volta vira recuperação:

```bash
python scripts/simulate_alerts.py [.env]
```

Ele parte do payload real de cada provedor — fixture quando existe, ao vivo quando não —, injeta
uma degradação fiel à forma daquele provedor, roda o módulo real e conduz o resultado pela
`NotificationManager` real até os quatro canais, interceptando só o transporte. As degradações
ficam no script, não nos módulos: é conhecimento de diagnóstico, e código de produção não carrega
andaime para isso. A lógica de veredito é testável sem rede em `tests/test_simulate_alerts.py`.

Foi assim que a #49 apareceu: `aws` e `gcp` alertavam e nunca recuperavam, porque o payload
saudável vazio trocava de ramo na máquina de estado. Defeito real que o script achou, não execução
instável. Corrigido — componente que some do payload é reconciliado antes da escolha do ramo
(`app/core/notifications.py`, `_recover_vanished_services`).

## Aviso de arquivamento pendente

O job `Lint` roda também:

```bash
python scripts/check_openspec_archive.py
```

Ele lista as changes em `openspec/changes/` e destaca as que têm **todas** as tarefas concluídas e
ainda não foram arquivadas. **Avisa, não reprova** — uma change pode estar terminada e ainda não
deployada, e reprovar nesse caso puniria o caso honesto.

Quando o aviso aparecer e a change já estiver em produção, arquive num PR próprio, como manda o
Stage 3:

```bash
openspec validate --all --strict          # antes, para o archive não abortar num delta inválido
openspec archive <change-id> -y
```

Existe porque a dívida virou recorrente: as issues #1, #17 e #29 arquivaram cinco changes entre si,
sempre descobertas por alguém olhando. Verificado contra o histórico — o aviso teria aparecido nos
commits `27fa5eb`, `8825763` e `bc1c910`, que são exatamente os momentos em que cada dívida nasceu.

**Limite conhecido:** o aviso exige *todas* as caixas marcadas. As duas changes que a #1 arquivou
tinham tarefas de rollout desmarcadas — dependiam de acesso ao cluster — e não teriam sido
apontadas. Uma change entregue com tarefa de rollout em aberto continua dependendo de alguém olhar.

## Proteção da branch `main`

`main` tem um ruleset chamado `main-protection`, com duas regras **ativas**:

| Regra | Efeito |
|---|---|
| `deletion` | `main` não pode ser apagada |
| `non_fast_forward` | `main` não aceita force-push |

Nenhuma das duas interfere no pipeline: o `@semantic-release/git` empurra um commit no topo de
`main` — fast-forward — e nunca apaga nada.

### Por que os checks ainda não são obrigatórios

A intenção era exigir `Lint` e `Run Tests` para poder mergear, mas isso **quebraria o release**.
Required status checks valem também para push direto, e o `@semantic-release/git` empurra o commit
`chore(release)` direto em `main` usando o `GITHUB_TOKEN` padrão, que é o app embutido do Actions.

Esse app **não pode** ser adicionado como ator de bypass num ruleset de repositório:

```
$ gh api -X PUT .../rulesets/<id> -f bypass_actors='[{"actor_type":"Integration","actor_id":15368}]'
422  Actor GitHub Actions integration must be part of the ruleset source or owner organization
```

Isso foi testado ao vivo em 2026-08-16, não deduzido. Com as regras ligadas, um release real falhou
assim:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Changes must be made through a pull request.
 ! [remote rejected] HEAD -> main (push declined due to repository rule violations)
```

A falha é limpa — o plugin de git roda antes do de release, então nada de tag, release ou imagem
pela metade. Ainda assim, o pipeline para.

Rulesets de organização, onde o app do Actions seria bypass elegível, exigem plano **Team**;
`solvelab` está no Free (`403 Upgrade to GitHub Team`).

Resta **um** caminho: trocar o `GITHUB_TOKEN` por um token de GitHub App próprio da organização, e
adicionar esse App ao bypass do ruleset.

Enquanto isso não for decidido, um PR com check vermelho continua tecnicamente mergeável. Os checks
existem e são visíveis desde a #16 — o que falta é torná-los obrigatórios sem derrubar o release.

### Reverter ou ajustar

```bash
gh api /repos/solvelab/service-checker/rulesets            # listar
gh api -X DELETE /repos/solvelab/service-checker/rulesets/<id>   # remover
```
