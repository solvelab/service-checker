# Tasks

## 1. Fetch
- [x] 1.1 `UpstreamChallenged(UpstreamRejected)` em `app/core/impersonated_fetch.py`.
- [x] 1.2 `_fetch_once` reconhece `cf-mitigated: challenge` (sem diferenciar maiúsculas; tolera resposta sem cabeçalhos).
- [x] 1.3 Docstring do módulo cita o diagnóstico da #87.

## 2. Testes
- [x] 2.1 403 com o cabeçalho levanta `UpstreamChallenged`, `.status == 403`, motivo nomeia o desafio.
- [x] 2.2 403 sem o cabeçalho continua `UpstreamRejected` com o motivo de hoje.
- [x] 2.3 Desafio repetido; `fetch_retry` traz o motivo explícito; desafio seguido de 200 é sucesso.
- [x] 2.4 `steam` e `rockstar`: `MonitorStatus.ERROR` com o motivo novo.
- [x] 2.5 Motivo chega ao "last error" da notificação de falha de monitoramento.

- [x] 2.6 Bug-hunter: igualdade por item, `Headers` real do curl_cffi com linhas repetidas, cabeçalhos sem `items()`, desafio com 503 repetido e com 401/404/520 não repetido.

## 3. Documentação
- [x] 3.1 READMEs de `steam` e `rockstar`.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 Checagem ao vivo contra `steamstat.us` registrada no PR.
- [x] 4.3 `openspec validate update-fetch-challenge-reason --strict`.
