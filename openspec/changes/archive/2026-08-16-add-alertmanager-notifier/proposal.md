# Change: Add Alertmanager notification channel

## Why
Quem já opera um Alertmanager quer os incidentes upstream no mesmo lugar dos alertas internos, para
reaproveitar silenciamento, agrupamento, inibição e rotas de plantão que já existem. Hoje não há
caminho: apontar `WEBHOOK_URL` para `/api/v2/alerts` não funciona, porque o endpoint espera um array
de alertas com `labels` e o `WebhookNotifier` emite um objeto plano.

O registro de notifiers da #23 tornou o custo de adicionar um canal baixo, e o Google Chat (#24) já
validou o caminho com um canal real.

## What Changes
- Novo canal `alertmanager` em `app/notifications/alertmanager/`, implementando os quatro métodos do
  protocolo `Notifier`.
- Mapeamento do modelo do Service Checker para `labels` e `annotations`.
- `endsAt` explícito para representar o estado firing sem exigir retransmissão a cada ciclo — ver
  `design.md`, é a decisão central desta change.
- Labels estáticas opcionais, para que o Alertmanager consiga rotear.
- Configuração por env, desligada por default.

## Impact
- Affected specs: `notifications` (ADDED requirements).
- Affected code:
  - `app/notifications/alertmanager/` — canal novo (`notifier.py`, `__init__.py`, `README.md`)
  - `app/core/config.py` — `AlertmanagerConfig` e carregamento
  - `app/core/notifications.py` — uma linha de registro, mais o repasse de `repeat_minutes` ao canal
  - `app/notifications/README.md` — tabela de canais
  - `.env.example`, `docker-compose.yml`, `docker-compose-dev.yml`, `deployment.yaml`, `DOCKER.md`
  - `tests/test_alertmanager_notifier.py` — nova suíte
  - `scripts/simulate_notifications.py` — o canal entra na simulação de entrega
- Não-breaking: canal desligado por default; nenhum payload existente muda.
- Sem dependências novas.
