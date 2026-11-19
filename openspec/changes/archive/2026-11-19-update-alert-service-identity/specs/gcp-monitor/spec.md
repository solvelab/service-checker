## ADDED Requirements

### Requirement: Alert names the affected product
O item de cada incidente ativo do GCP SHALL ter `name` formado pelo produto e pelas regiões entre parênteses. O produto SHALL ser o `service_name` do incidente; quando `service_name` estiver vazio ou for "Multiple Products", SHALL usar os títulos distintos de `affected_products`, até três, seguidos de `+N` para o restante; sem nenhum dos dois, SHALL ser "Google Cloud incident". O produto SHALL ter no máximo 80 caracteres. A chave do alerta SHALL continuar sendo o `id` do incidente, com recuo para `number` e `uri` quando o `id` faltar.

#### Scenario: Produto único
- **WHEN** um incidente ativo tem `service_name` "Google Cloud Storage" em `us-east1`
- **THEN** o item traz `name` "Google Cloud Storage (us-east1)" e a linha entregue diz `Google Cloud Storage (us-east1): service_outage`

#### Scenario: Mesmo produto em duas regiões
- **WHEN** dois incidentes abertos têm `service_name` "Google Cloud Storage", um em `us-central1` e outro em `us-east1`
- **THEN** os dois itens têm nomes diferentes

#### Scenario: Vários produtos
- **WHEN** um incidente tem `service_name` "Multiple Products" e cinco `affected_products`
- **THEN** o produto lista os três primeiros e termina em `+2`

#### Scenario: Deploy com incidente aberto
- **WHEN** a versão nova sobe com um incidente do GCP já em alerta
- **THEN** a chave do alerta é a mesma e não sai um novo alerta
