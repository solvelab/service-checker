# Tasks

## 1. Implementação
- [x] 1.1 Catálogo de regiões e incidentes por região via `/proxy`, buscados só quando há incidente aberto ou filtro.
- [x] 1.2 `regions` no item e no texto do alerta.
- [x] 1.3 `SERVICE_FILTER` por região, item `page` fora com filtro, filtro inválido em ERROR.
- [x] 1.4 Fallback sem região com `region_lookup_failed`.

## 2. Documentação
- [x] 2.1 README do módulo e `.env.example`.

## 3. Testes
- [x] 3.1 Região no texto, incidente multi-região, filtro que exclui, filtro inválido, `/proxy` fora, formato mudado, sem incidente sem requisição extra.
- [x] 3.2 Simulação ao vivo contra a página real (catálogo e incidentes por região) e cartão com região no Google Chat de teste.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate update-elevenlabs-regions --strict`.
- [x] 4.3 Em produção, o módulo segue `OK` sem requisição extra quando não há incidente. Verificado em 2026-11-19 (didevlab/housek8s#528): `elevenlabs` OK e nenhum `region_lookup_*` no log da v2.17.0.
