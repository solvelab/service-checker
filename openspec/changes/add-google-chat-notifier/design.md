## Context
O contrato do webhook de entrada do Google Chat, verificado em 2026-08-16:

- `POST https://chat.googleapis.com/v1/spaces/<SPACE_ID>/messages?key=<KEY>&token=<TOKEN>`
- A URL inteira é credencial: `token` é único por webhook e a documentação alerta explicitamente
  contra publicá-la.
- Corpo aceita `{"text": "..."}` ou `{"cardsV2": [...]}`.
- **`cardsV2` `textParagraph` usa tags HTML** — `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`,
  `<br>`, `<a href>`, `<ul>`, `<li>` — e **não** o markup de asteriscos do Chat.
- O campo `text` simples usa markup próprio: `*negrito*`, `_itálico_`, `` `mono` ``, `<url|texto>`.
- Threading por `{"thread": {"threadKey": "<chave>"}}` mais `messageReplyOption` na query string.
- Cota: **1 requisição por segundo por espaço**, compartilhada entre todos os webhooks do espaço.

## Goals / Non-Goals
- Goals
  - Entregar os quatro eventos do ciclo de vida num espaço do Chat, visualmente distintos.
  - Não perder mensagem por estourar a cota do espaço.
  - Nunca vazar a URL do webhook, que é credencial.
- Non-Goals
  - Cards interativos com botões — exigem app do Chat, não webhook de entrada.
  - Menção a usuários.
  - Alterar Telegram, webhook ou qualquer payload existente.

## Decisions

### `cardsV2`, não `text`
A escolha inicial parecia estética. Não é: o `textParagraph` do card usa **tags HTML**, então o
escape necessário é o mesmo `html.escape` que o projeto já aplica no Telegram. O campo `text`
exigiria um escapador novo para o markup do Chat — `*`, `_`, `` ` `` e `<` teriam semântica —, e
escapador novo é superfície nova de defeito.

O card também resolve dois requisitos de graça: o `header` dá título e subtítulo distintos por tipo
de evento, e um widget por incidente dá o mapeamento um-para-um que o campo `text` só conseguiria
com lista manual.

O card é montado como dict Python, não via Jinja2: a estrutura é JSON, e template de texto para
gerar JSON é como o projeto não faz em lugar nenhum.

### Ritmo em vez de fila
Módulos por serviço disparam **uma notificação por componente**: um provider com 12 componentes
degradados produz 12 despachos num único ciclo. A 1 req/s, isso estoura a cota.

O notifier guarda o instante do último envio e aguarda o restante do intervalo antes do próximo.
Simples, limitado e sem ciclo de vida a gerenciar.

O custo é real e precisa ser dito: `_dispatch` percorre os canais sequencialmente, então esperar
dentro do Google Chat atrasa os canais seguintes daquele evento. Com intervalo default de 60s e
raras rajadas, o atraso é tolerável. A alternativa — fila drenada por task de fundo — exige
start/stop que a aplicação não tem padrão para, e foi descartada por ora.

HTTP 429 é logado, não retentado: retentar dentro de um canal já limitado por ritmo só empilha
atraso. O ritmo é a prevenção; o 429 é o sinal de que ele foi insuficiente.

### Thread por `check_id`
`check_id` já é chave estável por componente — foi para isso que a #6 o endureceu. Usá-lo como
`threadKey` coloca o alerta e a recuperação do mesmo componente na mesma conversa, que é o que o
plantão quer ler.

Fica configurável e ligado por default. Desligado, cada mensagem abre conversa nova.

### A URL é credencial
Ela carrega `key` e `token` na query string. O caminho natural de erro — incluir a URL na mensagem
de log quando o POST falha — vazaria a credencial no stdout, que vai para agregador de log.

Todo log deste canal referencia o espaço por um identificador derivado, nunca a URL. Há teste
explícito garantindo que nenhuma linha de log contém `key=` ou `token=`.

## Risks / Trade-offs
- **Vazamento de credencial** → teste que varre todas as chamadas de log procurando a URL.
- **Cota estourada** → ritmo interno; 429 logado com o nome do canal.
- **Ritmo atrasa outros canais** → aceito e documentado; revisitar se virar problema real.
- **Schema do card errado** → o Chat responde 4xx com `google.rpc.Status`; o corpo do erro é logado
  (sem a URL) para diagnóstico.

## Migration Plan
Nenhuma. Canal desligado por default; quem não configurar nada não vê diferença.
