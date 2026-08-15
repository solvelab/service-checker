## REMOVED Requirements
### Requirement: Cfx Status Monitor
**Reason**: O endpoint `https://status.cfx.re/api/v2/summary.json` foi descomissionado após a aquisição do Cfx.re pela Rockstar Games e retorna 404 permanente. A nova fonte oficial cobre os serviços Cfx.re (FiveM, RedM) junto com os demais serviços Rockstar e é endereçada pelo novo monitor `rockstar`.
**Migration**: Operadores devem substituir a entrada `cfx` na config por `rockstar`, mantendo `service_filter` equivalente (ex.: `["FiveM", "RedM"]`) para preservar a cobertura anterior. Não há compatibilidade retroativa do slug `cfx`.
