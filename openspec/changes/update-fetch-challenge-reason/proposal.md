# Change: Name the anti-bot challenge in the failure reason

## Why
Issue #94. `steam` e `rockstar` buscam HTML por `app/core/impersonated_fetch.py`, e `_fetch_once`
transforma toda resposta `>= 400` em `UpstreamRejected("upstream returned HTTP <status>")`. Esse texto
vira o motivo do `MonitorStatus.ERROR` e, passado o limiar, o "last error" da notificação de falha de
monitoramento.

O diagnóstico da #87 (2026-11-16) mostrou o que o 403 do `steamstat.us` é:

```
server: cloudflare
cf-mitigated: challenge
<title>Just a moment...</title>
```

É um desafio do Cloudflare Bot Management: o operador do site configurou a borda para desafiar clientes
automatizados, e a taxa muda com o tempo sem nada mudar do nosso lado (63% de recusa em 2026-11-15, 0%
em 2026-11-16, mesmo perfil e mesmo IP). "HTTP 403" manda o operador investigar rede, checker ou
provedor, quando a causa é uma política de terceiro que não há como consertar daqui.

## What Changes
- `_fetch_once` reconhece o desafio pela resposta: status `>= 400` com `cf-mitigated: challenge` (sem
  diferenciar maiúsculas) levanta `UpstreamChallenged`, subclasse de `UpstreamRejected`, com o status
  preservado e motivo explícito.
- O valor é comparado por igualdade em cada item da lista (`block, challenge` conta;
  `non-challenge` não), porque o motivo afirma `cf-mitigated: challenge`.
- A repetição segue o status, como hoje: um desafio com 403, 429 ou 5xx de `RETRYABLE_STATUS` é
  repetido, porque parte dele passa na tentativa seguinte (#87); com 401, 404 ou 52x, não. Continua
  contando como `ERROR`. O `fetch_retry` e o motivo final trazem o texto novo.
- Um 403 sem o cabeçalho continua como hoje.
- Não muda: título, evento ou status de webhook da notificação, e labels do Alertmanager. O checker de
  fato não enxerga a fonte, então continua sendo falha de monitoramento; só o motivo fica certo.
- Fora de escopo, por decisão registrada na #87: contornar o desafio trocando perfis de imitação.

## Impact
- Specs afetadas: `steam-monitor` (MODIFIED: *Controlled failure path*), `rockstar-monitor`
  (MODIFIED: *Controlled Failure Path*).
- Código afetado: `app/core/impersonated_fetch.py`, READMEs de `steam` e `rockstar`, testes.
