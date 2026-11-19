## ADDED Requirements

### Requirement: Page indicator item cites the open incidents
O item do indicador da página (`id` "page") SHALL levar em `incidents` os títulos, até três, dos incidentes abertos que alertam neste ciclo, já com as regiões quando conhecidas.

#### Scenario: Indicador com incidente
- **WHEN** o indicador está em `minor` e há um incidente aberto "Degraded performance of Voice Library voices" na região EU
- **THEN** o item do indicador traz `incidents` ["Degraded performance of Voice Library voices [EU]"]
