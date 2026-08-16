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
