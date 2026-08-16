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
