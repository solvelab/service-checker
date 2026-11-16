# Tasks

## 1. Fixture
- [x] 1.1 Capturar o feed real com o evento encerrado (`end_time` presente) e dois ativos em
  `tests/fixtures/aws/current_events_with_ended.json`.

## 2. Monitor
- [x] 2.1 `_parse_event` carrega `ended_at`.
- [x] 2.2 `_evaluate_rule` descarta evento com `end_time` válido e não futuro.
- [x] 2.3 `end_time` ilegível mantém o evento ativo e gera log.
- [x] 2.4 Reescrever o comentário de `monitor.py` que afirma que o feed não tem encerramento.

## 3. Testes
- [x] 3.1 Fixture real: evento encerrado em us-east-1 não alerta; ativos fora do filtro não alertam.
- [x] 3.2 Evento sem `end_time` com `[RESOLVED]` no resumo continua alertando.
- [x] 3.3 `end_time` ilegível e `end_time` futuro mantêm o alerta.
- [x] 3.4 Ciclo ALERT → evento ganha `end_time` → recuperação emitida pelo `NotificationManager`.

## 4. Documentação
- [x] 4.1 `app/modules/aws/README.md`: critério de encerramento.

## 5. Validação
- [x] 5.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 5.2 `python scripts/simulate_endpoints.py .env.example` e `python scripts/simulate_alerts.py`
  rodados; resultado do `aws` registrado no PR.
- [x] 5.3 `openspec validate update-aws-resolved-events --strict`.
