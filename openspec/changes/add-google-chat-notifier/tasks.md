# Tasks

## 1. Config
- [x] 1.1 `GoogleChatConfig` em `app/core/config.py` com `enabled`, `webhook_url`, `thread_by_check`
      e o intervalo mínimo entre envios.
- [x] 1.2 Carregamento a partir de `GOOGLE_CHAT_*`, desligado por default, no padrão dos demais.
- [x] 1.3 Campo `google_chat` em `NotificationConfig`.

## 2. Canal
- [x] 2.1 `app/notifications/google_chat/notifier.py` com os quatro métodos do protocolo `Notifier`.
- [x] 2.2 Montagem do `cardsV2` como dict, com `header` por tipo de evento e um widget por
      incidente, consumindo `MonitorResult.reason_items`.
- [x] 2.3 Escape HTML do conteúdo dinâmico nos campos de texto do card. Mais slug em `cardId`
      e `threadKey`, que são chaves e não texto exibido — o id de componente vem do provedor.
- [x] 2.4 Ritmo de envio respeitando a cota de 1 req/s por espaço.
- [x] 2.5 `threadKey` derivado do `check_id`, configurável, com `messageReplyOption` na query.
- [x] 2.6 Tratamento de erro sem vazar a URL, incluindo 429 e 4xx com corpo de erro.
- [x] 2.7 Registrar o canal em `NotificationManager.__init__`.

## 3. Config files e docs
- [x] 3.1 `GOOGLE_CHAT_*` em `.env.example`, nos dois compose, no `deployment.yaml` e no `DOCKER.md`.
- [x] 3.2 `app/notifications/google_chat/README.md` no padrão dos demais canais.
- [x] 3.3 Tabela de canais em `app/notifications/README.md` e navegação dos READMEs irmãos.

## 4. Testes & Bug-Hunter
- [x] 4.1 Cada um dos quatro eventos produz mensagem, com cabeçalho distinto dos outros três.
- [x] 4.2 Alerta com N incidentes rende N itens.
- [x] 4.3 Incidente cujo texto contém `,`, `;` e `|` permanece um item só.
- [x] 4.4 Conteúdo com `<`, `>`, `&` e tag HTML é escapado e não altera a estrutura do card.
- [x] 4.5 **Vazamento de credencial:** nenhuma chamada de log contém a URL, `key=` ou `token=`,
      em nenhum caminho de erro.
- [x] 4.6 Rajada de N notificações no mesmo ciclo respeita o ritmo configurado.
- [x] 4.7 HTTP 429 é logado sem levantar e sem retentar.
- [x] 4.8 HTTP 4xx com corpo de erro é logado, com o corpo e sem a URL.
- [x] 4.9 Timeout e erro de rede não propagam exceção.
- [x] 4.10 Canal desabilitado não faz requisição alguma.
- [x] 4.11 `threadKey` é estável entre alerta e recuperação do mesmo componente.
- [x] 4.12 Com threading desligado, nenhum `thread` vai no corpo.
- [x] 4.13 O canal satisfaz `isinstance(x, Notifier)` e passa por `register()`.
- [x] 4.14 Regressão: a suíte inteira verde, sem alteração de asserção existente.

## 5. Validação & Fechamento
- [x] 5.1 `openspec validate add-google-chat-notifier --strict`.
- [x] 5.2 `ruff check app tests scripts`.
- [x] 5.3 `pytest tests/ -v`.
- [x] 5.4 `scripts/simulate_notifications.py` com o canal incluído, provando entrega dos quatro
      eventos e isolamento.
- [x] 5.5 `scripts/simulate_endpoints.py` sem regressão.
