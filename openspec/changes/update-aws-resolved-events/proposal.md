# Change: An event the AWS feed marks as ended stops counting as active

## Why
Issue #86. O monitor `aws` trata todo evento presente no feed `currentevents` como ativo. A premissa
está escrita no código (`app/modules/aws/monitor.py:122-123`, "it exposes no end timestamp and no
resolved entries (verified 2026-08-15)") e na spec, requisito *Presence in the feed means the event
is active*: "a fonte não publica marca de encerramento".

A premissa deixou de valer. Em 2026-11-15 o feed passou a manter um evento encerrado, com campos que
os eventos ativos não trazem:

```
arn:      arn:aws:health:us-east-1::event/MULTIPLE_SERVICES/AWS_MULTIPLE_SERVICES_OPERATIONAL_ISSUE/...
date:     1794746282   (2026-11-15 12:38:02 UTC)
end_time: 1794754409   (2026-11-15 14:53:29 UTC)   -- chave ausente nos dois eventos ativos
status:   "0"          -- eventos ativos: "3"
summary:  "[RESOLVED] Increased Connectivity issues in US-EAST-1"
```

Em produção (`de-service-checker`, imagem `v2.8.9`), o módulo seguiu em `ALERT` com esse motivo por
mais de 9 horas e 540 checagens depois do encerramento, e repetiu o alerta 69 vezes no Telegram e 70
no Alertmanager. O all-clear não sai, porque o payload nunca fica saudável: o evento continua no feed.

É o padrão que a spec cita no seu propósito, com o sinal invertido: antes o módulo lia campos que o
feed não publicava; agora o feed publica um campo que o módulo não lê.

## What Changes
- Um evento com `end_time` preenchido, numérico e não posterior ao instante da checagem é tratado
  como encerrado e não entra em `active_events`.
- Um evento sem `end_time` continua ativo enquanto estiver no feed, como hoje.
- Um `end_time` que não é um epoch válido em segundos mantém o evento ativo e é logado: um campo que
  não se sabe ler não pode apagar um incidente. Válido é finito, positivo e abaixo de `1e11`: `"0"`
  ou `"-1"` encerrariam um evento vivo, e um epoch em milissegundos o manteria ativo para sempre sem
  rastro (achados do bug-hunter).
- `scripts/simulate_alerts.py` descarta eventos encerrados do feed ao vivo ao montar a degradação,
  senão um feed só com eventos encerrados não injetaria degradação nenhuma.
- Um `end_time` no futuro mantém o evento ativo. O feed nunca mostrou isso, e a leitura conservadora
  é a que não silencia incidente.
- `_parse_event` passa a carregar `ended_at` (o epoch como string, par de `started_at`).
- `[RESOLVED]` no `summary` e `status == "0"` **não** decidem: o primeiro é texto livre, o segundo é
  a severidade não documentada que a spec já proíbe usar como critério.

## Impact
- Specs afetadas: `aws-monitor` (MODIFIED: *Presence in the feed means the event is active*).
- Código afetado: `app/modules/aws/monitor.py`, `app/modules/aws/README.md`,
  `scripts/simulate_alerts.py`, `tests/test_aws_monitor.py`, `tests/test_simulate_alerts.py`,
  fixture novo em `tests/fixtures/aws/`.
- Sem variável de configuração nova; sem mudança no `NotificationManager`.
