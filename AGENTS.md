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

Então exigir os checks depende de resolver antes **como a automação de release se autentica**. Dois
caminhos:

1. Trocar o `GITHUB_TOKEN` por um token de GitHub App próprio da organização, e adicionar esse app
   ao bypass do ruleset.
2. Criar o ruleset no nível da **organização**, onde o app do Actions é ator de bypass elegível.

Enquanto isso não for decidido, um PR com check vermelho continua tecnicamente mergeável. Os checks
existem e são visíveis desde a #16 — o que falta é torná-los obrigatórios sem derrubar o release.

### Reverter ou ajustar

```bash
gh api /repos/solvelab/service-checker/rulesets            # listar
gh api -X DELETE /repos/solvelab/service-checker/rulesets/<id>   # remover
```
