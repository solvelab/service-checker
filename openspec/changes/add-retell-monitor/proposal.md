# Change: Retell AI status monitor

## Why
Issue #116. A Retell AI não é monitorada: uma degradação da plataforma de chamadas não chega a nenhum canal. A página `status.retellai.com` é uma Statuspage da Atlassian. Na sondagem de 2026-11-18, `api/v2/summary.json` respondeu com 15 componentes, 3 deles de grupo, e `incidents/unresolved.json` e `scheduled-maintenances/active.json` responderam 200.

## What Changes
- Módulo novo `retell`, no molde do `bitbucket`:
  - regras `status`, `keyword` e `regex`;
  - `RETELL_SERVICE_FILTER`;
  - o motivo do alerta é enriquecido com os incidentes e as manutenções ativas;
  - falha controlada.
- URL default `https://status.retellai.com/api/v2/summary.json` para o slug `retell`.
- Com a regra `status`, um valor que não nomeia nenhum estado de componente da Statuspage cai nos estados degradados default. O default genérico de `RULE_VALUE` (`major,minor`) não bate com nenhum estado de componente, e o módulo nunca alertaria com a configuração mínima.

## Impact
- Affected specs: `retell-monitor` (nova)
- Affected code: `app/modules/retell/`, `app/core/config.py`, docs de configuração (`.env.example`, `DOCKER.md`, `deployment.example.yaml`, `README.md`, `docker-compose*.yml`, a navegação dos READMEs dos módulos) e `tests/test_retell_monitor.py`.
