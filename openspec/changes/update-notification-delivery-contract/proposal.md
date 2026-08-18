# Change: Notification channels must report whether they delivered

## Why
A issue #71 instalou um portão: o estado do alerta só avança quando alguém recebeu. Ele está em
`app/core/notifications.py:249` e `:540`, na forma `if not delivered: return`, e existe para que uma
queda de um ciclo no canal não faça o throttle suprimir um alerta que ninguém viu.

O portão não pode fechar. `_dispatch` deriva `delivered` de o canal ter levantado exceção:

```python
try:
    await getattr(notifier, method)(**kwargs)
    delivered = True
except Exception as exc:  # noqa: BLE001
    logger.error("notification channel failed", ...)
```

E nenhum canal levanta — por contrato. O `Notifier` Protocol, em `app/core/types.py:54`, manda o
contrário: *"Implementations own their transport failures: an HTTP error is logged and swallowed,
never raised."* Os quatro canais obedecem: `webhook/notifier.py:66`, `telegram/notifier.py:96`,
`google_chat/notifier.py:102` e `alertmanager/notifier.py:103` capturam, registram em log e retornam
normalmente. `webhook` sequer inspeciona o `status_code`; os outros três inspecionam, logam e seguem.

Logo `delivered` é `True` em toda falha real de entrega. O único caminho em que o portão fecha é um
canal que levanta — e o único do repositório que faz isso é o canal deliberadamente quebrado que
`scripts/simulate_notifications.py` registra para provar isolamento. A simulação passa exercitando
exatamente o caso que não acontece em produção.

É o modo de falha assinatura desta base, aplicado ao conserto dela: o caminho de degradação existe,
funciona, e nada diz que ele foi tomado. O `AGENTS.md` chama isso de "o fallback funciona, o sinal
não" — aqui o sinal que falta é o do próprio mecanismo que deveria dar sinal.

## What Changes
- `Notifier` Protocol passa a devolver `bool` nos quatro métodos `send_*`: `True` quando pelo menos
  um destino aceitou, `False` quando nenhum aceitou. A docstring que hoje manda engolir a falha é
  reescrita: a falha continua não sendo levantada, mas passa a ser **reportada**.
- Os quatro canais implementam o retorno, para os dois modos de falha: exceção de transporte e
  resposta com `status >= 400`. O `webhook` ganha inspeção de status, que hoje não tem.
- `_dispatch` consome o retorno em vez de inferir da ausência de exceção, e mantém o isolamento:
  um canal que não entregou não interrompe o laço.
- Um retorno que não seja `bool` é tratado como não-entrega e registrado como violação de contrato —
  em vez de virar `True` por engano, que é o defeito de hoje em outra roupa.
- `tests/test_notifier_contract.py`: guarda que exige de cada canal registrado o retorno booleano nos
  dois caminhos, para que um canal novo não reabra o vão.

Decisões que moldam o desenho:

- **`bool` de retorno, não exceção.** Levantar inverteria o contrato do Protocol e faria cada canal
  depender do `try` de quem chama para não derrubar os outros. O isolamento já existe em
  `_dispatch`; o que falta é informação, não proteção.
- **Sucesso parcial conta como entrega.** O Telegram envia para N `chat_id`s. Se um chat recebeu,
  alguém viu, e reenviar no ciclo seguinte duplicaria a mensagem para quem já leu. `True` quando
  pelo menos um destino aceitou.
- **`status >= 400` é não-entrega em todos os canais.** Hoje `google_chat` e `alertmanager` já
  inspecionam e logam; o que muda é a consequência. O `webhook` passa a inspecionar.
- **Nenhuma retentativa dentro do ciclo.** O contrato do projeto é retentar no ciclo seguinte, que é
  o que o portão restaurado passa a fazer sozinho.
- **`None` não é sucesso.** Um canal que não devolve `bool` está fora de contrato; contá-lo como
  entrega é reproduzir o defeito. Ele é contado como não-entrega e a violação é logada.

## Impact
- Affected specs: `notifications` (requisito adicionado).
- Affected code:
  - `app/core/types.py` — Protocol e docstring do contrato
  - `app/core/notifications.py` — `_dispatch`
  - `app/notifications/{webhook,telegram,google_chat,alertmanager}/notifier.py`
  - `app/notifications/README.md` e o README de cada canal
  - `scripts/simulate_notifications.py` — veredito deixa de passar por vacuidade
  - `tests/test_notifier_contract.py` — novo; `tests/test_delivery_state.py` — casos novos
- **Breaking para implementação externa de canal.** Não há nenhuma: o conjunto é fechado no
  construtor de `NotificationManager` (`app/core/notifications.py:37-46`). Um canal registrado por
  terceiro que devolva `None` passa a contar como não-entrega, e isso é logado.
- Sem dependência nova.
- Fora de escopo: fila persistente de entrega, backoff interno, e o caso `2xx` sem entrega real
  (webhook aceito e mensagem descartada pelo destino), que nenhum sinal disponível distingue.
