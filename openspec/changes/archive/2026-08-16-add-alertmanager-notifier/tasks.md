# Tasks

## 1. Config
- [x] 1.1 `AlertmanagerConfig` em `app/core/config.py`: `enabled`, `url`, `token`, `header_name`,
      `resolve_after_seconds`, `extra_labels`.
- [x] 1.2 Carregamento a partir de `ALERTMANAGER_*`, desligado por default.
- [x] 1.3 Parser de `ALERTMANAGER_EXTRA_LABELS` no formato `k=v,k=v`, tolerante a entrada malformada.
- [x] 1.4 Campo `alertmanager` em `NotificationConfig`.

## 2. Canal
- [x] 2.1 `app/notifications/alertmanager/notifier.py` com os quatro métodos do protocolo.
- [x] 2.2 Corpo como array de alertas, com `labels`, `annotations`, `startsAt` e `endsAt` em RFC3339.
- [x] 2.3 `alertname` fixo por tipo; identidade em `check_id`, `module` e `component`.
- [x] 2.4 `endsAt` futuro nos eventos firing, com margem default `2 × repeat + 2 × interval`.
- [x] 2.5 `endsAt` no passado nos eventos de recuperação.
- [x] 2.6 Texto livre só em annotations.
- [x] 2.7 Labels estáticas mescladas, sem poder sobrescrever as reservadas.
- [x] 2.8 Tratamento de erro sem propagar exceção.
- [x] 2.9 Registrar o canal em `NotificationManager.__init__`, repassando `repeat_minutes`.

## 3. Config files e docs
- [x] 3.1 `ALERTMANAGER_*` nos cinco arquivos de configuração.
- [x] 3.2 `app/notifications/alertmanager/README.md`, incluindo o smoke manual contra um
      Alertmanager real e a explicação do prazo de expiração.
- [x] 3.3 Tabela de canais em `app/notifications/README.md`.

## 4. Testes & Bug-Hunter
- [x] 4.1 Alerta de serviço produz array de um alerta com `alertname` preenchido.
- [x] 4.2 Recuperação envia `endsAt` no passado.
- [x] 4.3 Labels idênticas entre dois ciclos do mesmo incidente.
- [x] 4.4 Dois incidentes distintos geram labels distintas.
- [x] 4.5 Falha e recuperação de monitoramento chegam, distinguíveis por label.
- [x] 4.6 `endsAt` firing excede `repeat + interval`, provando ausência de flapping.
- [x] 4.7 Timestamps validam como RFC3339 com timezone.
- [x] 4.8 `reason` e `message` não aparecem em nenhuma label.
- [x] 4.9 Labels estáticas aparecem; tentativa de sobrescrever reservada é ignorada.
- [x] 4.10 HTTP 4xx/5xx e timeout logados sem levantar.
- [x] 4.11 URL ausente pula sem postar.
- [x] 4.12 O token não aparece em log.
- [x] 4.13 O canal satisfaz `isinstance(x, Notifier)` e passa por `register()`.
- [x] 4.14 Regressão: suíte inteira verde, sem alteração de asserção existente.

## 5. Validação & Fechamento
- [x] 5.1 `openspec validate add-alertmanager-notifier --strict`.
- [x] 5.2 `ruff check app tests scripts`.
- [x] 5.3 `pytest tests/ -v`.
- [x] 5.4 `scripts/simulate_notifications.py` com o canal incluído.
- [x] 5.5 `scripts/simulate_endpoints.py` sem regressão.
