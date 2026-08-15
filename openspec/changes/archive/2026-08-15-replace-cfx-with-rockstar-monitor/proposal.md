# Change: Replace cfx monitor with Rockstar Services monitor

## Why
O endpoint `https://status.cfx.re/api/v2/summary.json` retorna 404 desde a aquisição do Cfx.re pela Rockstar Games. `https://status.cfx.re/` agora redireciona para `https://support.rockstargames.com/servicestatus`, que é a nova fonte oficial de status para os serviços Cfx.re (FiveM, RedM) junto com os demais serviços Rockstar (GTA Online, Red Dead Online, Social Club, Rockstar Games Launcher). O monitor `cfx` atual está em estado `ERROR` permanente em produção, gerando ruído e perdendo cobertura real do serviço.

## What Changes
- **BREAKING** Remover o módulo `cfx` (`app/modules/cfx/`) e seu registro/configuração default.
- Adicionar novo módulo `rockstar` (`app/modules/rockstar/`) consumindo a página oficial de status da Rockstar (`support.rockstargames.com/servicestatus`) com o(s) endpoint(s) JSON identificado(s) durante a investigação da task 1.1.
- Suportar filtro por serviço (ex.: `FiveM`, `RedM`, `GTA Online`, `Social Club`) via configuração `service_filter` reaproveitando o padrão existente.
- Normalizar severidades upstream (`up`/`limited`/`down`/`info`/`maintenance` ou equivalentes) para `OK` / `ALERT` / `ERROR` consistentes com os demais monitores.
- Atualizar config default e documentação para apontar para o monitor `rockstar` no lugar de `cfx`.

## Impact
- Affected specs: `cfx-monitor` (remoção), `rockstar-monitor` (criação).
- Affected code:
  - `app/modules/cfx/` — removido
  - `app/modules/rockstar/` — novo módulo (`monitor.py`, `__init__.py`, `README.md`)
  - `app/core/config.py` — registro de slug, URL default, mapeamento de regras
  - `config.example.yaml` / configs equivalentes — substituir entrada `cfx` por `rockstar`
  - Deployment/ConfigMap em uso no cluster — operador atualizará pós-merge
- Breaking: usuários com `cfx` habilitado precisam migrar config para `rockstar` (item de migração em `tasks.md`).
