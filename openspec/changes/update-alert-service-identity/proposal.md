# Change: Every alert names the impacted service

## Why
Issue #146. Depois da v2.18.0 (#142), conferido com os dados reais em 2026-11-19: OpenAI, Retell e Bitbucket já nomeiam o componente no cartão, mas três casos continuam genéricos ou induzem a erro:
- o item do GCP não tem `name`, e o cartão diz só `Google Cloud — Service outage`, embora o `incidents.json` traga `service_name` e `affected_products`;
- o item do indicador do ElevenLabs diz só `ElevenLabs — Minor`;
- um incidente que não lista componentes (incident.io, como a OpenAI) aparece em todo componente em alerta com o mesmo rótulo de um incidente que cita aquele componente.

## What Changes
- **GCP.** O item ganha `name`: o produto (`service_name`, ou, quando ele é "Multiple Products" ou vazio, os títulos de `affected_products`, até 3, depois `+N`) e as regiões entre parênteses, como `Google Cloud Storage (us-central1)`. A região vai no nome porque o manager remonta a linha do alerta a partir dele, e dois incidentes do mesmo produto precisam ser distinguíveis. A chave do alerta continua o `id` do incidente.
- **ElevenLabs.** O item do indicador leva em `incidents` os títulos dos incidentes abertos que alertam, já com as regiões; o cartão não repete o provedor quando o componente tem o mesmo nome.
- **Incidente sem componente.** `attach_incidents` mantém todos os títulos em `incidents`, como na v2.18.0, e repete em `page_incidents` os que não listam componente. O cartão mostra esses com o rótulo `Open page incident (no component listed)`. Nenhum dos dois é guardado no estado do alerta.

## Impact
- Affected specs: `gcp-monitor`, `elevenlabs-monitor`, `notifications`.
- Affected code: `app/modules/gcp/monitor.py`, `app/modules/elevenlabs/monitor.py`, `app/core/statuspage.py`, `app/core/notifications.py`, `app/notifications/google_chat/notifier.py`, docs.
- Sem migração: chaves de estado, `threadKey` e rótulos do Alertmanager usam o `id`, que não muda.
