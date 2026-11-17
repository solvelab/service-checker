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

## 5. Validação
- [x] 5.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 5.2 `openspec validate add-challenge-tolerance --strict`.
- [ ] 5.3 Em produção, um dia de desafio do `steam` não abre falha de monitoramento abaixo da tolerância.
