# Change: Persist notification state across restarts

## Why
`_alert_state` e `_error_state` vivem num dicionário do processo. Um restart apaga os dois, e a consequência não é cosmética: **um incidente que atravessa o restart nunca recebe o all-clear**. O operador é avisado de que um componente caiu e, se o processo reiniciar antes do provedor se recuperar, o "resolvido" simplesmente não chega. De fora é indistinguível de um incidente ainda em curso.

Nenhum provedor anuncia duas vezes. Se a degradação terminou enquanto o processo estava fora, o payload volta saudável, o estado está vazio e não há transição a notificar — o mesmo modo de falha que a reconciliação de componente ausente corrigiu, chegando por outra estrada.

O defeito apareceu ao **falhar em provar** algo. Durante o rollout em produção um alerta foi forçado de propósito e disparou nos dois canais; reverter a configuração reiniciou o pod, e com ele foi o estado, então a recuperação não pôde ser demonstrada ponta a ponta. Com GitOps e `selfHeal` ligados, qualquer commit no manifesto reinicia o processo — a janela em que um incidente perde o all-clear ficou maior, não menor.

## What Changes
- Novo `app/core/state_store.py`: lê e escreve o estado pendente como um documento JSON.
- `NotificationManager` carrega o estado na construção e grava ao fim de cada `handle_result`.
- `handle_result` vira um invólucro fino sobre `_handle_result`, para que o flush cubra todos os `return` por construção em vez de por disciplina.
- Duas configurações novas: `NOTIFICATION_STATE_PATH` (vazio = só em memória, comportamento antigo) e `NOTIFICATION_STATE_MAX_AGE_MINUTES` (default 1440).
- Volume nomeado nos dois compose, montado em `/var/lib/service-checker`.

Decisões que moldam o desenho:

- **Arquivo JSON, não banco.** O daemon não tem banco, não tem servidor HTTP e roda com uma réplica. Um arquivo escrito atomicamente e relido na subida é a menor coisa que fecha o vão.
- **Só alertas pendentes são gravados.** Uma entrada OK é escrituração transitória que a máquina de estado já descarta; persistir ressuscitaria chaves recém-removidas.
- **Nunca quebrar o monitor.** Arquivo ilegível, JSON inválido, schema de outra versão, disco cheio: tudo é logado e engolido. Um monitor que não sobe porque não conseguiu persistir escrituração é pior que um que esquece.
- **Escrita só quando muda.** O caso comum é todo provedor saudável e o documento idêntico ciclo após ciclo; sem isso seriam dez `fsync` por minuto para não dizer nada.

## Impact
- Affected specs: `notifications` (requisito adicionado).
- Affected code:
  - `app/core/state_store.py` — novo
  - `app/core/notifications.py` — carga na construção, flush no invólucro
  - `app/core/config.py` — duas opções novas
  - `tests/test_state_persistence.py` — nova suíte
  - `.env.example`, `docker-compose.yml`, `docker-compose-dev.yml`, `README.md`, `DOCKER.md`
- **Não-breaking.** Sem `NOTIFICATION_STATE_PATH` nada muda: o store é um no-op que carrega vazio e nunca escreve. As 636 asserções existentes seguem verdes sem alteração.
- Sem dependência nova: `json`, `os`, `tempfile` e `pathlib` são biblioteca padrão.
- Fora de escopo: alta disponibilidade e múltiplas réplicas. O deploy é `replicas: 1`, e estado compartilhado entre réplicas é problema diferente e maior.
