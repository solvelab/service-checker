# Change: Challenge tolerance

## Why
Issue #112. O `steamstat.us` fica atrás do Cloudflare Bot Management, que desafia o checker numa taxa que muda sozinha.
Em 2026-11-17, entre 05:53Z e 11:53Z:
- 219 das 344 checagens do `steam` (63%) terminaram em `UpstreamChallenged`, mesmo com 3 tentativas cada.
- As sequências de erro duraram até 45 minutos, e cada uma passou do limiar de 3.
- Foram 6 aberturas de falha de monitoramento e 21 `monitor_failure`, sem nenhum incidente da Steam.

O aviso diz que a fonte recusou o robô, não que a Steam caiu.

## What Changes
- Os módulos `steam` e `rockstar` marcam o resultado `ERROR` cuja causa é `UpstreamChallenged` (`MonitorResult.challenged`).
- Uma sequência de erros formada só por resultados marcados não anuncia a falha de monitoramento enquanto a duração, contada desde o primeiro erro da sequência, for menor que `NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES`. O default é `60`, e `0` desliga a tolerância.
- Um erro não marcado que chega a uma sequência só de desafio ainda não anunciada encerra a parte de desafio e começa uma sequência nova, contada a partir dele, no limiar normal. Antes da #114 a contagem dos desafios era herdada: em 2026-11-18 às 11:43:20Z, o primeiro `DNSError` de uma queda do roteador chegou ao `steam` com contagem 5 e anunciou a falha 1min35s antes de a queda de conectividade abrir.
- Uma sequência absorvida que termina fica só no log (`monitor_challenge_absorbed`), sem `monitor_blind_window`.
- A tolerância vale só para uma falha ainda não anunciada. A repetição de uma falha já anunciada segue como hoje.
- O estado da sequência sobrevive a restart, nos campos novos de `MonitorErrorState` no `state.json`, sem subir `SCHEMA_VERSION`.
- Não muda: a detecção de queda de conectividade (#106), a histerese da #87, o contrato de quatro métodos dos canais e a política de retentativa de `fetch_html`.

## Impact
- Affected specs: `notifications`
- Affected code: `app/core/types.py`, `app/core/notifications.py`, `app/core/config.py`,
  `app/core/state_store.py`, `app/main.py`, `app/modules/steam/monitor.py`,
  `app/modules/rockstar/monitor.py`, docs de configuração e `scripts/simulate_notifications.py`.
