# Tasks

## 1. Contrato
- [x] 1.1 `Notifier` Protocol (`app/core/types.py`): os quatro `send_*` passam a devolver `bool`.
- [x] 1.2 Reescrever a docstring do Protocol: a falha continua não sendo levantada, mas é reportada.

## 2. Canais
- [x] 2.1 `webhook`: inspecionar `status_code` e devolver `False` em `>= 400` e em exceção.
- [x] 2.2 `telegram`: devolver `True` se ao menos um `chat_id` aceitou; `False` se nenhum.
- [x] 2.3 `google_chat`: devolver `False` em exceção e em `status >= 400`.
- [x] 2.4 `alertmanager`: idem.

## 3. Dispatch
- [x] 3.1 `_dispatch` consome o retorno; `delivered` só é `True` com aceite de algum canal.
- [x] 3.2 Retorno fora de contrato (não-`bool`) conta como não-entrega e é logado.
- [x] 3.3 Isolamento preservado: canal que falha não interrompe o laço.
- [x] 3.4 Nenhum canal registrado continua avançando o estado.

## 4. Testes & Bug-Hunter
- [x] 4.1 Teste que falha contra o código atual: POST do canal falha e o alerta é contado como entregue.
- [x] 4.2 Um teste por canal, para exceção de transporte e para `status >= 400`.
- [x] 4.3 Sucesso parcial no Telegram conta como entrega.
- [x] 4.4 `_alert_state` não avança sem entrega, e o ciclo seguinte reenvia.
- [x] 4.5 Guarda de contrato: todo canal em `app/notifications/*/notifier.py` devolve `bool`.
- [x] 4.6 Registrar no PR quais testes caem contra o commit anterior.

## 5. Documentação
- [x] 5.1 `app/notifications/README.md` e o README de cada canal descrevem o contrato de retorno.
- [x] 5.2 `scripts/simulate_notifications.py`: veredito reprova canal que falhe em silêncio.

## 6. Validação & Fechamento
- [x] 6.1 `ruff check app tests scripts`
- [x] 6.2 `pytest tests/ -v`
- [x] 6.3 `python scripts/simulate_notifications.py`
- [x] 6.4 `openspec validate update-notification-delivery-contract --strict`
