# Change: Monitoring recovery waits for sustained success

## Why
Issue #87. O `NotificationManager` anuncia a falha de monitoramento depois de
`NOTIFICATION_ERROR_THRESHOLD` checagens `ERROR` seguidas, e anuncia a recuperação na **primeira**
checagem bem-sucedida. `_clear_monitor_error` faz `pop` do `MonitorErrorState` inteiro, inclusive do
`last_notified_at`, e com isso a próxima rajada de erros já não cai no throttle de
`NOTIFICATION_REPEAT_MINUTES`.

Um upstream que oscila vira spam. Medido nos logs de produção do `de-service-checker` (`v2.8.9`),
2026-11-15 10:07 a 2026-11-16 00:10 UTC, módulo `steam`:

```
814 checagens: 366 ERROR (HTTP 403 depois de 3 tentativas), 446 OK, 2 ALERT
51 monitor_failure + 47 monitor_failure_resolved; 101 mensagens no Telegram e 102 no Alertmanager
76 monitor_blind_window
```

Rodando a mesma sequência de 814 resultados pela regra proposta (limiar de erro 3, repetição 10 min):

| Sucessos exigidos (M) | Avisos de falha | Avisos de recuperação |
|---|---|---|
| 1 (hoje) | 51 | 47 |
| 2 | 38 | 22 |
| 3 | 37 | 15 |
| 5 | 37 | 7 |

(A tabela usa o `NotificationManager` real sobre a sequência gravada; com M = 3 a nova página
dentro de uma falha aberta exige também uma nova sequência de N erros, além da janela de
repetição.)

Os avisos de falha que sobram com M = 3 são, na maior parte, repetições a cada 10 minutos de uma
falha que continua aberta. Isso é o comportamento que a spec já pede: o `steam` ficou cego em 45% das
checagens. O ruído que some é o "restabelecido" de um único `OK` no meio de uma rajada.

O mesmo log mostra um segundo defeito. Os 76 `monitor_blind_window` dizem "no channel accepted the
notification", mas 53 das sequências de erro tinham 1 erro e 23 tinham 2, abaixo do limiar 3. Nessas,
nenhuma notificação chegou a ser tentada. O log existe para registrar uma falha que nenhum canal
recebeu (#71); disparado para toda rajada abaixo do limiar, ele afirma uma coisa falsa e afoga o caso
real.

A causa do 403 também foi medida nos mesmos logs: 63% de recusa na primeira tentativa (o docstring
de `impersonated_fetch.py` registra 30%), e só 18% das recusas somem na segunda tentativa (o docstring
registra 83%). A recusa ficou persistente, não transitória. Testar outros perfis de imitação a partir
desta máquina foi bloqueado pela política de permissões da sessão, então o perfil não muda neste
change. A medição fica registrada no docstring, para quem for atacar a causa.

## What Changes
- Variável nova `NOTIFICATION_RECOVERY_THRESHOLD` (default `3`, piso `1`): quantas avaliações
  bem-sucedidas seguidas encerram uma falha de monitoramento **notificada**.
- Durante essa janela, um novo `ERROR` mantém a falha aberta e reinicia a contagem de erros: uma
  nova notificação de falha exige N erros seguidos **e** `NOTIFICATION_REPEAT_MINUTES` desde o último
  envio (o `last_notified_at` é preservado). Assim o texto "N consecutive failed checks" continua
  verdadeiro, e um erro isolado no meio da oscilação não repagina.
- O restabelecimento só remove o estado quando algum canal aceita; recusado, o próximo sucesso
  tenta de novo (antes, o estado saía antes do envio e o aviso se perdia).
- Entradas de erro persistidas passam pelos mesmos limites das de alerta: `last_notified_at` no
  futuro ou mais velho que `NOTIFICATION_STATE_MAX_AGE_MINUTES` é descartado.
- Uma falha nunca notificada continua sendo encerrada pelo primeiro sucesso, como hoje.
- `monitor_blind_window` só é logado quando a sequência chegou ao limiar, ou seja, quando um envio
  foi tentado e nenhum canal aceitou.
- `MonitorErrorState` ganha `consecutive_successes`, persistido pelo `StateStore` (estado antigo sem
  o campo carrega como `0`).
- O docstring de `app/core/impersonated_fetch.py` registra a medição de 2026-11-15.

## Impact
- Specs afetadas: `notifications` (MODIFIED: *Notify monitoring recovery*, *Preserve pending alert
  state across monitoring failures*; ADDED: *Monitoring recovery hysteresis*, *Blind window is
  logged only for an attempted notification*).
- Código afetado: `app/core/notifications.py`, `app/core/config.py`, `app/core/state_store.py`,
  `app/core/impersonated_fetch.py`, testes, `DOCKER.md`, `.env.example`, `deployment.example.yaml`,
  `docker-compose.yml`, `docker-compose-dev.yml`, `app/notifications/README.md`, `README.md`.
- Comportamento novo por default: o aviso de monitoramento restabelecido chega `M - 1` ciclos mais
  tarde (2 minutos com o intervalo de 60 s). `NOTIFICATION_RECOVERY_THRESHOLD=1` restaura o anterior.
