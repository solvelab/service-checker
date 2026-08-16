# Change: Add Google Chat notification channel

## Why
O projeto entrega notificação por Telegram e por webhook genérico. Times que operam no Google
Workspace não têm caminho: apontar `WEBHOOK_URL` para um webhook de entrada do Google Chat não
funciona, porque o Chat espera um corpo próprio e não o objeto plano que o `WebhookNotifier` emite.

O registro de notifiers entregue em #23 tornou o custo de adicionar um canal baixo — um arquivo
novo e uma linha de registro, sem tocar no despacho.

## What Changes
- Novo canal `google_chat` em `app/notifications/google_chat/`, implementando os quatro métodos do
  protocolo `Notifier`.
- Renderização como `cardsV2`, com cabeçalho distinto por tipo de evento e um widget por incidente.
- Ritmo de envio respeitando a cota de 1 requisição por segundo por espaço.
- Agrupamento opcional em thread por `check_id`, para alerta e recuperação do mesmo componente
  caírem na mesma conversa.
- Configuração por env, desligada por default.

## Impact
- Affected specs: `notifications` (ADDED requirements).
- Affected code:
  - `app/notifications/google_chat/` — canal novo (`notifier.py`, `__init__.py`, `README.md`)
  - `app/core/config.py` — `GoogleChatConfig` e carregamento
  - `app/core/notifications.py` — uma linha de registro
  - `app/notifications/README.md` — tabela de canais
  - `.env.example`, `docker-compose.yml`, `docker-compose-dev.yml`, `deployment.yaml`, `DOCKER.md`
  - `tests/test_google_chat_notifier.py` — nova suíte
  - `scripts/simulate_notifications.py` — o canal entra na simulação de entrega
- Não-breaking: canal desligado por default; nenhum payload existente muda.
- Sem dependências novas — `httpx` já está em `requirements.txt`.
