# Tasks

## 1. Implementação
- [x] 1.1 Config `SERVICE_MONITOR_LIVENESS_PATH` (default `/tmp/service-checker-alive`; vazio desliga).
- [x] 1.2 `app/core/liveness.py`: janela por módulo, task que grava o arquivo e loga as transições.
- [x] 1.3 `app/healthcheck.py` e ligação em `app/main.py`.

## 2. Documentação
- [x] 2.1 `.env.example`, `docker-compose*.yml`, `deployment.example.yaml` (probes), `DOCKER.md`, `README.md`.

## 3. Testes
- [x] 3.1 Janela por módulo, módulo atrasado, subida sem checagem, path vazio, falha de escrita, códigos de saída do healthcheck.
- [x] 3.2 Execução real: app rodando, healthcheck `0`; `kill -STOP`, healthcheck `1` depois de 120 s.
- [x] 3.3 bug-hunter no diff.

## 4. Validação
- [x] 4.1 `ruff check app tests scripts` e `pytest tests/` verdes.
- [x] 4.2 `openspec validate add-liveness-probe --strict`.
- [x] 4.3 Em produção o probe passa, e `kill -STOP` no processo faz o kubelet reiniciar o pod. Verificado em 2026-11-19 (didevlab/housek8s#526): probe passa; SIGSTOP pelo nó k8s-04 levou o kubelet a reiniciar o container em ~4,5 min.
