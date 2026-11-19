# Tasks

## 1. Implementação
- [x] 1.1 Config: `NOTIFICATION_STARTUP_ANNOUNCE`, `NOTIFICATION_STARTUP_TIMEOUT_SECONDS`, `NOTIFICATION_HEARTBEAT_TIME` e `NOTIFICATION_HEARTBEAT_TIMEZONE`, com aviso para valor recusado.
- [x] 1.2 Retrato por módulo no `NotificationManager`: status, motivo, última checagem e desde quando o status vale.
- [x] 1.3 Protocolo `StatusReporter` e `send_status_report` em Telegram, webhook e Google Chat.
- [x] 1.4 Agenda do aviso de subida (primeira rodada ou prazo), do heartbeat (próxima ocorrência) e do retry, ligada em `main`/`scheduler`.

## 2. Documentação
- [x] 2.1 `.env.example`, `docker-compose*.yml`, `deployment.example.yaml`, `DOCKER.md`, `README.md` e READMEs dos canais.

## 3. Testes
- [x] 3.1 Testes do retrato, da agenda (módulo lento, prazo, horário e fuso, restart no mesmo dia), do retry e de cada canal (conteúdo, falha, credencial fora do log).
- [x] 3.2 `scripts/simulate_notifications.py` com o relatório: Telegram, webhook e Google Chat receberam 2 relatórios cada (subida e heartbeat), Alertmanager 0, e os três reportaram "not delivered" contra um endpoint que responde 500. Execução real em 2026-11-19 (`python -m app` com `oci,elevenlabs,retell,bitbucket`, `BITBUCKET_SERVICE_FILTER` errado e o webhook apontado para um receptor local): aviso de subida às 04:37:08Z, 1s depois da subida, com `bitbucket` em `ERROR` (`no target components matched filter`); heartbeat às 04:39:00Z, o minuto configurado (01:39 em São Paulo).
- [x] 3.3 bug-hunter no diff: 9 ataques. Corrigidos com teste: o horário era comparado como relógio de parede e não como instante, o que repetia o heartbeat na hora repetida do fim do horário de verão e o perdia na hora pulada do início; o texto do Telegram passava de 4096 caracteres com uns 16 provedores em `ERROR` (agora os motivos encolhem até caber); dois envios simultâneos ao Google Chat passavam juntos pela cota (agora a cota e o POST ficam sob um lock); uma exceção montando o relatório encerrava o heartbeat para sempre; e as configurações lidas não apareciam no log de `startup`. Ficam como lacuna: o "desde" do heartbeat conta a partir da subida depois de um restart; um sleep longo atrasa o heartbeat se o host suspender; um CrashLoop manda um aviso de subida por volta.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate add-startup-and-heartbeat-notifications --strict`.
- [x] 4.3 Em produção, o pod novo manda o aviso de subida ao Telegram com os 12 provedores. Evidência: pod de 2026-11-19T05:03:29Z (didevlab/housek8s#522), `startup_announced` às 05:03:33Z com `12 OK`, entregue ao grupo do Telegram.
