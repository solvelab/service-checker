# Change: ElevenLabs incidents carry their region, and the filter selects regions

## Why
Issue #136. status.elevenlabs.io tem 4 regiões, cada uma uma sub-página incident.io (`united-states`, `eu`, `india`, `singapore`). O módulo lê só `api/v2/summary.json`, que agrega os incidentes de todas e não diz de qual é cada um. Em 2026-11-19 o alerta "Degraded performance of Voice Library voices" chegou sem região; ele era de EU, India e Singapore. `ELEVENLABS_SERVICE_FILTER` filtra componentes que a página não publica, então não faz nada.

A região só aparece na API de frontend do incident.io, sem documentação: `GET /proxy/<host>` traz o catálogo `subpages[]` (`subpath`, `name`), e `GET /proxy/<host>/incidents?subpage_slug=<slug>` os incidentes da região, com o mesmo `id` de `api/v2`. Um incidente pode estar em várias regiões.

## What Changes
- **Região no incidente.** Quando há incidente aberto no `summary.json`, o módulo busca o catálogo e os incidentes de cada região e acrescenta `regions` (nomes) a cada item `incident:<id>`. O texto do alerta passa a ser `<nome> [<regiões>]: <status>`. Continua um item por incidente.
- **Filtro por região.** `ELEVENLABS_SERVICE_FILTER` passa a nomear regiões, por `subpath` ou nome (sem diferenciar caixa); vazio = todas, como em `AWS_` e `OCI_SERVICE_FILTER`. Com filtro, só alerta incidente de pelo menos uma região filtrada, e o item `page` sai, porque o indicador agrega todas as regiões. Componentes publicados, que a página não tem hoje, deixam de ser filtrados.
- **Filtro inválido é ERROR.** Um valor do filtro que não está no catálogo vira ERROR com o motivo: a API devolve 200 vazio para slug inexistente, e o módulo ficaria cego.
- **Fallback.** Se o `/proxy` falhar ou mudar de formato, o alerta sai como hoje, sem região, com um `WARNING` `region_lookup_failed` no log. Com filtro, o incidente sem região conhecida também alerta: falso positivo é melhor que cegueira. Severidade e ciclo de vida continuam vindo do `summary.json`.

## Impact
- Affected specs: `elevenlabs-monitor`.
- Affected code: `app/modules/elevenlabs/monitor.py`, `app/modules/elevenlabs/README.md`, `scripts/simulate_endpoints.py`, `tests/test_elevenlabs_monitor.py` e fixtures, `.env.example`.
