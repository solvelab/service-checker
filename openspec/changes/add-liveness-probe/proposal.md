# Change: Liveness file for the Kubernetes probe

## Why
Issue #134. O service-checker não tem endpoint HTTP nem outro sinal de vida que o kubelet leia. Processo morto já reinicia o container; o caso que escapa é o loop travado — event loop bloqueado ou um módulo pendurado —, em que o pod segue `Running` e calado até o heartbeat das 09:00 (#124). A empresa exige probes declarados. O arquivo de estado não serve: `state_store.py` não regrava documento igual.

## What Changes
- **Arquivo de vida.** Uma task grava `SERVICE_MONITOR_LIVENESS_PATH` (default `/tmp/service-checker-alive`; vazio desliga) a cada 30 s, **só** quando todo módulo carregado completou um ciclo dentro da sua janela: `3 × intervalo + pior caso de uma busca` (3 tentativas do timeout, mais o backoff), com piso de 15 minutos: o ciclo também espera a entrega de notificações, serializada entre módulos, e restart não conserta canal lento. Antes da primeira checagem, a referência de um módulo é a subida do processo, para o probe não matar o pod na largada. Um módulo atrasado e a volta ao normal são logados uma vez cada (`liveness_stale` / `liveness_fresh`); falha de escrita, também uma vez.
- **Comando do probe.** `python -m app.healthcheck [max_age_seconds]` lê o mesmo env, sai `0` quando o arquivo tem menos de `max_age` (default 120 s) e `1` caso contrário — inclusive arquivo ausente ou liveness desligado —, com o motivo em uma linha.
- **Docs** com `livenessProbe` e `readinessProbe` prontos.

## Impact
- Affected specs: `liveness` (nova).
- Affected code: `app/core/liveness.py` (novo), `app/healthcheck.py` (novo), `app/core/config.py`, `app/main.py`, docs de configuração.
