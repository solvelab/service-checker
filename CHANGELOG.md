## [2.8.2](https://github.com/solvelab/service-checker/compare/v2.8.1...v2.8.2) (2026-08-16)


### Bug Fixes

* **statuspage:** payload malformado vira ERROR, e a copia some ([#61](https://github.com/solvelab/service-checker/issues/61)) ([f06f6d2](https://github.com/solvelab/service-checker/commit/f06f6d267f0bbba96251faee503a2fd13c6726aa)), closes [#54](https://github.com/solvelab/service-checker/issues/54)

## [2.8.1](https://github.com/solvelab/service-checker/compare/v2.8.0...v2.8.1) (2026-08-16)


### Bug Fixes

* **logging:** parar de descartar extras e logar o estado onde da para ver ([#60](https://github.com/solvelab/service-checker/issues/60)) ([cca47ec](https://github.com/solvelab/service-checker/commit/cca47ecab8a75d2505bcd5ac570848c8c6272332)), closes [#59](https://github.com/solvelab/service-checker/issues/59)

# [2.8.0](https://github.com/solvelab/service-checker/compare/v2.7.0...v2.8.0) (2026-08-16)


### Features

* **notifications:** manter alertas pendentes entre reinicios ([#58](https://github.com/solvelab/service-checker/issues/58)) ([6e8a5a6](https://github.com/solvelab/service-checker/commit/6e8a5a6a2a1a681b0d71e84d2f80b3c2e38ff5eb)), closes [#57](https://github.com/solvelab/service-checker/issues/57)

# [2.7.0](https://github.com/solvelab/service-checker/compare/v2.6.1...v2.7.0) (2026-08-16)


### Features

* **cloudflare:** adicionar monitor com allowlist curada dos 475 componentes ([#53](https://github.com/solvelab/service-checker/issues/53)) ([1846b91](https://github.com/solvelab/service-checker/commit/1846b91b8f6581fab5b613e719dd87e5aff6101a)), closes [#51](https://github.com/solvelab/service-checker/issues/51)

## [2.6.1](https://github.com/solvelab/service-checker/compare/v2.6.0...v2.6.1) (2026-08-16)


### Bug Fixes

* **notifications:** emitir all-clear de componente que sai do payload ([#52](https://github.com/solvelab/service-checker/issues/52)) ([9e8469f](https://github.com/solvelab/service-checker/commit/9e8469fe13b214c265ede36efc01c524abec3ef8)), closes [#49](https://github.com/solvelab/service-checker/issues/49)

# [2.6.0](https://github.com/solvelab/service-checker/compare/v2.5.2...v2.6.0) (2026-08-16)


### Features

* **scripts:** simular disparo de alerta dos nove provedores ([#50](https://github.com/solvelab/service-checker/issues/50)) ([efa0f3d](https://github.com/solvelab/service-checker/commit/efa0f3d6836cc459bf1c064f23e1a0b0c398e20b)), closes [#49](https://github.com/solvelab/service-checker/issues/49) [#48](https://github.com/solvelab/service-checker/issues/48)

## [2.5.2](https://github.com/solvelab/service-checker/compare/v2.5.1...v2.5.2) (2026-08-16)


### Bug Fixes

* **scripts:** buscar cada endpoint uma vez por execucao ([3af3b4e](https://github.com/solvelab/service-checker/commit/3af3b4e896d6e06408003a4c7d4fcc5f6e6c49f5)), closes [#34](https://github.com/solvelab/service-checker/issues/34)

## [2.5.1](https://github.com/solvelab/service-checker/compare/v2.5.0...v2.5.1) (2026-08-16)


### Bug Fixes

* **scripts:** distinguir soluco de provedor de cegueira real na simulacao ([528060e](https://github.com/solvelab/service-checker/commit/528060e8fd06bbafbf550287c69ff152e9508570)), closes [#29](https://github.com/solvelab/service-checker/issues/29)

# [2.5.0](https://github.com/solvelab/service-checker/compare/v2.4.0...v2.5.0) (2026-08-16)


### Features

* **notifications:** adicionar Alertmanager como canal ([bc1c910](https://github.com/solvelab/service-checker/commit/bc1c910c590a61ae28fd67aefc45cbbef060aa53)), closes [#23](https://github.com/solvelab/service-checker/issues/23)

# [2.4.0](https://github.com/solvelab/service-checker/compare/v2.3.3...v2.4.0) (2026-08-16)


### Features

* **notifications:** adicionar Google Chat como canal ([8825763](https://github.com/solvelab/service-checker/commit/88257635aeb7c105a012189d6e7fc16da3e1d489))

## [2.3.3](https://github.com/solvelab/service-checker/compare/v2.3.2...v2.3.3) (2026-08-16)


### Bug Fixes

* **notifications:** um bullet por incidente no card do Telegram ([f96a417](https://github.com/solvelab/service-checker/commit/f96a417596af7cd910b46e8b25a7795435501db3))

## [2.3.2](https://github.com/solvelab/service-checker/compare/v2.3.1...v2.3.2) (2026-08-16)


### Bug Fixes

* **aws:** ler os campos que o feed realmente devolve ([4207df1](https://github.com/solvelab/service-checker/commit/4207df14b932320fa005068f33f782d00c811360))

## [2.3.1](https://github.com/solvelab/service-checker/compare/v2.3.0...v2.3.1) (2026-08-16)


### Bug Fixes

* **oci:** emitir id estavel por incidente e endurecer a chave de estado ([5bc50c9](https://github.com/solvelab/service-checker/commit/5bc50c947640b09c2e9c586be25aad6b6d5000f3))

# [2.3.0](https://github.com/solvelab/service-checker/compare/v2.2.3...v2.3.0) (2026-08-16)


### Features

* **notifications:** avisar quando o proprio monitoramento quebra ([27fa5eb](https://github.com/solvelab/service-checker/commit/27fa5eba7e423ed55b113339dc9762723d78884d))

## [2.2.3](https://github.com/solvelab/service-checker/compare/v2.2.2...v2.2.3) (2026-08-16)


### Bug Fixes

* **notifications:** nao descartar estado de alerta pendente num ciclo ERROR ([fe1c3d4](https://github.com/solvelab/service-checker/commit/fe1c3d406b71e6c4743441959ccd503db4443218))

## [2.2.2](https://github.com/solvelab/service-checker/compare/v2.2.1...v2.2.2) (2026-08-15)


### Bug Fixes

* **steam:** buscar via curl_cffi para contornar bloqueio TLS do Cloudflare ([57b6bfe](https://github.com/solvelab/service-checker/commit/57b6bfe5db3b1b0c47aa026518641d9d3b3025a6))

## [2.2.1](https://github.com/solvelab/service-checker/compare/v2.2.0...v2.2.1) (2026-08-15)


### Bug Fixes

* **config:** corrigir .env.example que referenciava o modulo cfx removido ([4e33ac5](https://github.com/solvelab/service-checker/commit/4e33ac5e5cfb7795965f637d9ab5dfbae0c928f5))

# [2.2.0](https://github.com/solvelab/service-checker/compare/v2.1.0...v2.2.0) (2026-04-28)


### Features

* **bitbucket:** habilitar modulo bitbucket no deployment k8s ([610f923](https://github.com/solvelab/service-checker/commit/610f923234feded1dee29a7f8d089dafdb5c98f6))

# [2.1.0](https://github.com/solvelab/service-checker/compare/v2.0.0...v2.1.0) (2026-04-28)


### Features

* **modules:** adicionar suporte ao monitoramento do Bitbucket ([e13cc61](https://github.com/solvelab/service-checker/commit/e13cc61fd9d3a4cdef7a804b8341cc86576c3cc4))

# [2.0.0](https://github.com/solvelab/service-checker/compare/v1.1.1...v2.0.0) (2026-04-26)


### Features

* **rockstar:** replace cfx monitor with Rockstar Games services monitor ([4739a92](https://github.com/solvelab/service-checker/commit/4739a9268a7c0295c2ac40d02e531ecd16678ffb))


### BREAKING CHANGES

* **rockstar:** the cfx module slug is removed. Operators must replace
CFX_* environment variables with ROCKSTAR_* (see docs/DOCKER.md and
app/modules/rockstar/README.md for migration details).

## [1.1.1](https://github.com/solvelab/service-checker/compare/v1.1.0...v1.1.1) (2026-02-09)


### Bug Fixes

* **deployment:** change imagePullPolicy to Always for service-checker container in deployment.yaml ([4475f77](https://github.com/solvelab/service-checker/commit/4475f77006a15fd856f23db01949ece69c5df899))
* **deployment:** update service-checker image tag to latest in deployment.yaml ([c16fc7b](https://github.com/solvelab/service-checker/commit/c16fc7b55483affeabca42bd85b521dceb146020))

# [1.1.0](https://github.com/solvelab/service-checker/compare/v1.0.2...v1.1.0) (2026-02-09)


### Bug Fixes

* **docker:** add GitHub monitor config to docker-compose files ([b9e16c8](https://github.com/solvelab/service-checker/commit/b9e16c83949a2a5081fa9998607a12f198dad970))
* **release:** support emoji prefixes in commit messages for semantic-release ([4073225](https://github.com/solvelab/service-checker/commit/4073225266680593f431d2bf875c58b460b3a6ce))
* **version:** update application version to 1.0.2 in `_version.py` ([455b5b1](https://github.com/solvelab/service-checker/commit/455b5b1fb5e8c466439d303fd3582296a13612a3))


### Features

* **deployment:** adicionar configuração de deployment ([192702b](https://github.com/solvelab/service-checker/commit/192702bb1cf2da88bce143c51760d123d1e57d12))
* **github:** add GitHub status monitoring module ([ef21a68](https://github.com/solvelab/service-checker/commit/ef21a6866fbc3f5d86c4d8e6693043282f5af02f))
* **logging, notifications:** enhance recovery notifications with status tracking ([f1c9e04](https://github.com/solvelab/service-checker/commit/f1c9e04f3231c1d8fc46beecf1cc940a778892ea))
* **README:** add logo and donation section ([a6da05f](https://github.com/solvelab/service-checker/commit/a6da05f1544cc06836e2eaaf394912b86c902bbf))
* **versioning:** introduce version tracking in the application ([92b20d1](https://github.com/solvelab/service-checker/commit/92b20d1779662bd65a48fd7563bc36c107df438d))

## [1.0.2](https://github.com/solvelab/service-checker/compare/v1.0.1...v1.0.2) (2025-12-25)


### Bug Fixes

* ignore steam pageviews alerts ([f334d53](https://github.com/solvelab/service-checker/commit/f334d53c93d6040fe24f053c0c81be00e691fe1e))

## [1.0.1](https://github.com/solvelab/service-checker/compare/v1.0.0...v1.0.1) (2025-12-25)


### Bug Fixes

* publish image after semantic release ([96fc836](https://github.com/solvelab/service-checker/commit/96fc836301e1ebce5a7f678ffdad9122fb0ae0d9))

# 1.0.0 (2025-12-25)


### Features

* initial release automation setup ([d8f6973](https://github.com/solvelab/service-checker/commit/d8f697350eb1655aced89ae5c85db79c0b9c6ea4))

# Changelog

All notable changes to this project will be documented in this file.
