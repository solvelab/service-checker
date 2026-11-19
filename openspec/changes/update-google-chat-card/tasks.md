# Tasks

## 1. Implementação
- [x] 1.1 `opened_at` no `AlertState` (manager e state store) e `started_at` no `MonitorResult`.
- [x] 1.2 Helper de incidentes abertos em `statuspage.py` (unresolved com recuo para incidents.json) e `incidents` nos itens dos 6 módulos.
- [x] 1.3 Cartão do Google Chat: título, incidentes, link, hora local, há quanto tempo, duração, mudança de estado; `GOOGLE_CHAT_TIMEZONE`.
- [x] 1.4 Webhook: `started_at` e `duration_seconds`.

## 2. Documentação
- [x] 2.1 README do Google Chat, README do webhook, `.env.example`, `DOCKER.md`.

## 3. Testes
- [x] 3.1 `opened_at` mantido na repetição, persistido e restaurado, recuo para estado antigo; cartão de alerta, repetição e recuperação; fuso; incidentes por componente e sem componentes; recuo 404; falha na busca.
- [x] 3.2 Evidência real: cartões de alerta, repetição e recuperação no Google Chat de teste.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate update-google-chat-card --strict`.
- [ ] 4.3 Em produção, o pod sobe na versão nova com o estado restaurado.
