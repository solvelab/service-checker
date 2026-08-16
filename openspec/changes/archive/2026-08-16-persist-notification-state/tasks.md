# Tasks

## 1. Implementation
- [x] 1.1 Criar `app/core/state_store.py` com `StateStore.load()` e `StateStore.save()`, escrita atômica e schema versionado.
- [x] 1.2 Persistir apenas alertas pendentes; entradas OK são escrituração transitória.
- [x] 1.3 Descartar na carga o que estiver além de `max_age` ou com instante no futuro.
- [x] 1.4 Carregar o estado na construção do `NotificationManager`.
- [x] 1.5 Transformar `handle_result` em invólucro sobre `_handle_result`, com o flush no `finally` — todos os `return` cobertos por construção.
- [x] 1.6 Não regravar quando o documento serializado não mudou.
- [x] 1.7 Duas opções em `app/core/config.py`, com default que preserva o comportamento antigo.

## 2. Wiring
- [x] 2.1 `NOTIFICATION_STATE_PATH` e `NOTIFICATION_STATE_MAX_AGE_MINUTES` no `.env.example`.
- [x] 2.2 Volume nomeado em `docker-compose.yml` e `docker-compose-dev.yml`, montado em `/var/lib/service-checker`.

## 3. Documentation
- [x] 3.1 Seção no `README.md` e bloco no `DOCKER.md`, incluindo o aviso de que `emptyDir` não serve.

## 4. Validation
- [x] 4.1 `tests/test_state_persistence.py` — 40 testes.
- [x] 4.2 Teste decisivo: alerta, instância nova lendo o arquivo, payload saudável, all-clear emitido. ✓
- [x] 4.3 Teste de controle: sem caminho configurado, o all-clear se perde exatamente como antes — senão 4.2 passaria por coincidência. ✓
- [x] 4.4 Bug-Hunter: arquivo ausente, vazio, truncado, lista em vez de objeto, versão futura, entrada sem instante, instante ilegível, instante ingênuo, vizinha inválida, `last_item` que não é objeto, contador negativo, caminho não gravável, escrita interrompida, temp órfão, nome com acento e aspas, idade zero.
- [x] 4.5 Mutação: `save()` como no-op derruba **15** dos 40; `load()` como no-op derruba **16**.
- [x] 4.6 `ruff check app tests scripts`. ✓
- [x] 4.7 `pytest tests/ -v` — 676 passed, sem regressão nas 636 anteriores. ✓
- [x] 4.8 `scripts/simulate_alerts.py` e `scripts/simulate_notifications.py` em exit 0. ✓
- [x] 4.9 `openspec validate persist-notification-state --strict`. ✓

## 5. Rollout
- [x] 5.1 Montar volume persistente no `deployment.yaml` do cluster e definir `NOTIFICATION_STATE_PATH`. ✓
  Entregue em didevlab/housek8s#39: `pvc-service-checker-state`, 1Gi em `longhorn-1r`, montado em
  `/var/lib/service-checker`. Junto foi preciso trocar a estratégia do Deployment para `Recreate` —
  com PVC `ReadWriteOnce`, o `RollingUpdate` trava porque o pod novo não monta o volume enquanto o
  antigo o segura.
- [x] 5.2 Confirmar em produção: alertar, reiniciar o pod, provedor recuperar, all-clear chegar. ✓
  Verificado em 2026-08-16, ponta a ponta:
  1. filtro apontado para um PoP degradado → `cloudflare status degraded`,
     `telegram notification sent`, `alertmanager notification sent`
  2. estado lido de dentro do pod: `{"alerts": {"cloudflare:2htqrtyxmmtr": {...}}}`
  3. pod reiniciado
  4. `recovery notification emitted`, `from_status: partial_outage -> to_status: operational`,
     `telegram notification sent` — confirmado no chat pelo operador
  O ciclo era impossível antes: o estado morria no restart.
