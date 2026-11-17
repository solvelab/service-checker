# Change: Connectivity loss detection

## Why
Issue #106. O roteador da casa onde o cluster roda reinicia de madrugada. Em 2026-11-16, por volta das
07:00Z (04:00 BRT), os 10 módulos falharam juntos, com 5 a 6 erros seguidos entre 07:00:15Z e
07:05:32Z. Cada um passou do limiar e gerou seu `monitor_failure` (07:02:19Z–07:03:22Z) e seu
`monitor_failure_resolved` (07:07Z–07:08Z). O resumo do Telegram ganhou 10 linhas de "04:02 → 04:08",
sem que nenhum provedor tivesse problema: quem caiu foi a rede do próprio checker. O
`NotificationManager` avalia cada módulo isoladamente e não tem como distinguir as duas coisas.

## What Changes
- Quando, no momento em que um módulo atinge o limiar de erro, a fração dos módulos carregados em erro
  é igual ou maior que `NOTIFICATION_CONNECTIVITY_QUORUM` (default `0.7`, com no mínimo 3 módulos), o
  manager abre uma **queda de conectividade** em vez de anunciar a falha do módulo. Enquanto ela está
  aberta, nenhuma falha de monitoramento por módulo é anunciada.
- A queda notifica uma única vez, como a falha de monitoramento de um módulo sintético `checker`,
  quando a duração atinge `NOTIFICATION_CONNECTIVITY_TOLERANCE_MINUTES` (default `10`; `0` notifica
  toda queda). O fim anuncia a recuperação com início, fim e duração. Abaixo da tolerância fica só o
  log (`connectivity_lost` / `connectivity_restored`).
- `NOTIFICATION_CONNECTIVITY_WINDOWS` (opcional), no fuso `NOTIFICATION_CONNECTIVITY_TIMEZONE`
  (default `America/Sao_Paulo`): uma queda que começa numa janela e termina antes do fim dela não
  notifica, seja qual for a duração. A janela só vale para a queda de conectividade; a falha isolada de
  um provedor notifica normalmente.
- A queda termina quando a fração em erro fica abaixo do quórum. Um módulo que segue em erro volta ao
  fluxo normal, com a contagem recomeçando do zero.
- O estado da queda vai para o `state.json` numa chave nova (`connectivity`), sem subir
  `SCHEMA_VERSION`.
- Não muda: detecção de `ALERT`, o contrato de quatro métodos dos canais, a histerese da #87.

## Impact
- Affected specs: `notifications`
- Affected code: `app/core/notifications.py`, `app/core/config.py`, `app/core/state_store.py`,
  `app/main.py`, docs de configuração e `scripts/simulate_notifications.py`.
