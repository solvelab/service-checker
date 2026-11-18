# Change: ElevenLabs status monitor

## Why
Issue #117. A ElevenLabs não é monitorada. A página `status.elevenlabs.io` é incident.io, não Statuspage, mas expõe uma API compatível. Na sondagem de 2026-11-18:
- `api/v2/summary.json` responde 200 com `page`, `status`, `components` e `incidents`.
- `components` vem vazio, e `incidents/unresolved.json` dá 404.
- Havia o indicador `minor` ("Partial System Degradation") e o incidente aberto `Increased latency and API errors` (`identified`, impacto `minor`).

Um módulo no molde do `claude` exige componentes e ficaria em `ERROR` em toda checagem.

## What Changes
- Módulo novo `elevenlabs`. A regra `status` lê o valor como a severidade mínima do indicador da página, na ordem `minor` < `major` < `critical`. O default genérico `major,minor` alerta em `minor`, `major` e `critical`. Um valor que não nomeia nenhuma severidade cai em `minor`.
- O alerta sai por item, com ciclo próprio de alerta e recuperação. Cada incidente aberto é um item `incident:<id>` enquanto estiver aberto, filtrado pelo próprio impacto. O item `page` existe enquanto o indicador estiver no limiar, ao lado dos incidentes. A primeira versão trocava um pelo outro, o que transformava uma queda numa sequência de alerta, recuperação e alerta.
- Um indicador vazio ou fora do vocabulário conhecido é `ERROR`.
- Se a fonte passar a publicar componentes, um componente degradado também vira item, com `ELEVENLABS_SERVICE_FILTER`.
- Faltando `status.indicator`, o resultado é `ERROR`, porque é sinal de mudança de formato da fonte. `components` vazio não é erro.
- `keyword` e `regex` como nos outros módulos.

## Impact
- Affected specs: `elevenlabs-monitor` (nova)
- Affected code: `app/modules/elevenlabs/`, `app/core/config.py` (URL default), docs de configuração, navegação dos READMEs, contagem de provedores, `scripts/simulate_alerts.py`, `scripts/simulate_endpoints.py`, `tests/test_elevenlabs_monitor.py` e `tests/fixtures/elevenlabs/`.
