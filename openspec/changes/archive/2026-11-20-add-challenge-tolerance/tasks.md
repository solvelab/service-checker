# Tasks

## 1. Resultado e config
- [x] 1.1 `MonitorResult.challenged`; `steam` e `rockstar` marcam o `ERROR` vindo de `UpstreamChallenged`.
- [x] 1.2 `NotificationConfig.challenge_tolerance_minutes` com `NOTIFICATION_CHALLENGE_TOLERANCE_MINUTES` (default `60`); valor inválido avisa e volta ao default; a variável sai no log de startup.

## 2. Manager
- [x] 2.1 `MonitorErrorState.challenge_only` e `challenge_absorbed`, persistidos no `state.json`.
- [x] 2.2 No limiar, uma sequência só de desafio abaixo da tolerância não anuncia; um erro não marcado volta ao limiar normal.
- [x] 2.3 A sequência absorvida termina com `monitor_challenge_absorbed` no log, sem `monitor_blind_window`.
- [x] 2.4 O anúncio depois da tolerância leva o início real da sequência.

## 3. Testes
- [x] 3.1 Replay das checagens do `steam` de 2026-11-17 05:53Z–11:53Z: nenhum `monitor_failure` com tolerância `60`.
- [x] 3.2 Sequência só de desafio passando da tolerância; sequência mista; tolerância `0`; `rockstar`.
- [x] 3.3 Restart no meio de uma sequência absorvida; config inválida; falha já anunciada segue repetindo.
- [x] 3.4 Suíte atual sem mudança.
- [x] 3.5 bug-hunter no diff: 8 ataques. Foram corrigidos com teste o relógio reiniciado por uma queda de conectividade, o início no futuro que segurava o aviso para sempre, o reenvio que perdia o início, os módulos em desafio contando para o quórum e o log `monitor_challenge_absorbed` numa sequência que deixou de ser só de desafio. O contrato do webhook foi documentado. Ficam como lacuna o restart depois de uma parada longa, que gasta a tolerância com o tempo parado, e o arquivo anterior à #112 no meio de uma sequência, que avisa uma vez.

## 4. Documentação e simulação
- [x] 4.1 `.env.example`, `DOCKER.md`, `deployment.example.yaml`, `app/notifications/README.md`, READMEs do `steam` e do `rockstar`.
- [x] 4.2 `scripts/simulate_notifications.py` com o replay passando pelo manager e pelo resumo do Telegram.

## 5. Virada para sequência mista (#114)
- [x] 5.1 Na virada de só desafio para mista, ainda não anunciada e com tolerância ligada, a parte de desafio termina (com `monitor_challenge_absorbed` se foi retida) e uma sequência nova começa no erro.
- [x] 5.2 Testes: replay de 2026-11-18 11:38Z–12:02Z com queda de conectividade; virada abaixo do limiar; virada já retida; falha já anunciada; tolerância `0`; restart na sequência nova.
- [x] 5.3 `scripts/simulate_notifications.py` com o caso da virada; `app/notifications/README.md`.
- [x] 5.4 bug-hunter no diff: 5 ataques. Corrigido com teste o reinício depois da tolerância vencida, que apagava um envio já recusado (sem `monitor_blind_window`) e a retentativa dele. Fixados como escolha: um erro solto entre sequências retidas não avisa (como `EEO`), e desafios depois de uma sequência reiniciada contam no limiar normal, como em qualquer sequência mista. Uma queda do roteador no meio de um desafio longo reinicia o relógio da tolerância depois da queda, como previsto.

## 6. Validação
- [x] 6.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 6.2 `openspec validate add-challenge-tolerance --strict`.
- [x] 6.3 Em produção, um dia de desafio do `steam` não abre falha de monitoramento abaixo da tolerância. Evidência: 2026-11-20, pod v2.20.0 desde 00:59:30Z, 17 `monitor_challenge_absorbed` do `steam`, 0 `monitor_failure`, 0 `monitor_blind_window`; queda de conectividade 07:00:15–07:07:49Z durante desafio do `steam` (desde 06:57:12Z) sem `monitor_failure` (#112, #114).
