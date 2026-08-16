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

## Verificação automática

Todo pull request para `main` executa `Lint` (`ruff check app tests scripts`) e `Run Tests`
(`pytest tests/ -v`) via `.github/workflows/ci.yml`. Corte de versão e publicação da imagem só
acontecem no push para `main`, depois do merge — um PR nunca cria tag nem publica no GHCR.

Antes de abrir o PR, rode os dois localmente com Python 3.11 e as dependências fixadas em
`requirements.txt`. Para exercitar os endpoints reais além das fixtures:

```bash
python scripts/simulate_endpoints.py .env.example
```

Esse script consulta **nove provedores reais**, então é diagnóstico e não gate determinístico.
Um módulo que falha é reexecutado antes de o script declarar falha: falha que não se repete sai
como `TRANSIENT` e não derruba a execução, falha que se repete sai não-zero. `SIMULATE_ATTEMPTS=1`
desliga a repetição.

Não coloque esse script na CI esperando sinal estável — um provedor instável por alguns minutos
ainda vai deixar a execução vermelha. Para verificar a entrega de notificação, que não toca a rede
e é determinística:

```bash
python scripts/simulate_notifications.py
```

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
