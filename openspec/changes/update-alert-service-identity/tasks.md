# Tasks

## 1. Implementação
- [x] 1.1 GCP: `name` a partir de `service_name` / `affected_products`, e `reason_items` com o nome.
- [x] 1.2 ElevenLabs: `incidents` no item do indicador; cartão sem provedor repetido.
- [x] 1.3 `attach_incidents` separa `incidents` de `page_incidents`; rótulo próprio no cartão; nenhum dos dois no `last_item`.

## 2. Documentação
- [x] 2.1 README do GCP, do ElevenLabs, do Google Chat e do webhook.

## 3. Testes
- [x] 3.1 Fixture do payload real do GCP (produto único e "Multiple Products"); indicador do ElevenLabs com incidentes; incidente com e sem componente; chave do alerta inalterada.
- [x] 3.2 Evidência real: cartões no Google Chat de teste.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate update-alert-service-identity --strict`.
- [ ] 4.3 Em produção, o pod sobe na versão nova sem re-alerta.
