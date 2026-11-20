# Tasks

## 1. Implementação
- [x] 1.1 `_thread_key` com o início do episódio quando o resultado traz `started_at`.

## 2. Documentação
- [x] 2.1 Seção de threading do README do Google Chat.

## 3. Testes
- [x] 3.1 Episódio inteiro numa thread; dois episódios em threads diferentes; mesma chave depois de restart; sem `started_at` mantém a chave antiga.
- [x] 3.2 Evidência real: dois episódios do mesmo componente em threads separadas no Google Chat de teste.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate update-chat-thread-per-episode --strict`.
- [x] 4.3 Em produção, o pod sobe na versão nova sem re-alerta. Evidência: v2.20.0 em 2026-11-19, pod Running/Ready, `state_load` sem descarte, 0 alertas e 0 linhas ERROR após a subida, healthcheck ok.
