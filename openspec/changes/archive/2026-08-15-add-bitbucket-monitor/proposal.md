# Change: Add Bitbucket Status monitor

## Why
Times que dependem de Bitbucket Cloud (Atlassian) para repositórios Git, Pull Requests e Pipelines de CI ficam cegos a incidentes upstream sem um monitor dedicado. A Atlassian publica o status oficial via Statuspage v2 em `https://bitbucket.status.atlassian.com/api/v2/summary.json` — o mesmo formato JSON já consumido pelos monitores `github`, `openai` e `claude`. Adicionar Bitbucket à esteira fecha uma lacuna relevante de cobertura DevOps com esforço mínimo, reaproveitando totalmente o padrão Statuspage existente.

## What Changes
- Adicionar novo módulo `bitbucket` (`app/modules/bitbucket/`) consumindo `api/v2/summary.json` da Statuspage Atlassian.
- Suportar as três estratégias de regra existentes (`status`, `keyword`, `regex`) e filtro por componente via `BITBUCKET_SERVICE_FILTER`.
- Enriquecer alertas com incidentes (`api/v2/incidents/unresolved.json`) e manutenções (`api/v2/scheduled-maintenances/active.json`), espelhando o monitor `github`.
- Registrar URL default em `app/core/config.py` (`_default_url`).
- Criar README do módulo no padrão dos demais e atualizar barras de navegação dos READMEs irmãos.
- Adicionar suíte de testes `tests/test_bitbucket_monitor.py` cobrindo healthy, degraded, filtro, enriquecimento, falhas de rede e regras.

## Impact
- Affected specs: `bitbucket-monitor` (criação).
- Affected code:
  - `app/modules/bitbucket/` — novo módulo (`monitor.py`, `__init__.py`, `README.md`)
  - `app/core/config.py` — branch adicional em `_default_url`
  - `app/modules/{steam,openai,claude,github,rockstar,oci,gcp,aws}/README.md` — link Bitbucket adicionado na navegação
  - `tests/test_bitbucket_monitor.py` — nova suíte
- Não-breaking: módulo é desligado por default (não consta em `SERVICE_MONITOR_MODULES` default `["steam"]`); operadores ativam adicionando `bitbucket` à env var.
- Sem novas dependências (`httpx` já presente; sem necessidade de `curl_cffi` — endpoint JSON público responde a clientes padrão).
