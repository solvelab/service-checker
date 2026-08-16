"""Busca de HTML com fingerprint TLS de navegador, e retry para o que é transitório.

Dois provedores publicam status em HTML atrás da Cloudflare — `steam` e `rockstar` — e
os dois precisam do `curl_cffi` para imitar o handshake TLS do Chrome. Sem isso a borda
devolve 403 **sempre**, e foi o que deixou o `steam` cego por meses.

Resolvido aquilo, sobrou outro: a borda recusa **às vezes**. Medido em produção e
reproduzido daqui:

    20 requisições a steamstat.us com impersonate=chrome124
    respostas: {200: 14, 403: 6}          -> 30% de rejeição
    retry após 1.5s resolveu 5 de 6       -> 83% dos 403 somem na segunda tentativa

A rejeição chega **mais rápido** que o sucesso — 129ms contra 278ms —, o que é a borda
recusando na hora, não o upstream sofrendo. Com verificação a cada 60s, três recusas
seguidas acontecem sozinhas e disparam a notificação de monitor morto: o operador recebe
um incidente que não existe, e aprende a ignorar o canal.

Duas regras moldam o que está aqui:

- **Repetir só o que é transitório.** 403, 429 e 5xx, mais erro de rede. Um 404 ou um
  corpo vazio não melhoram com insistência, e repetir só atrasa o diagnóstico.
- **Repetição não é silêncio.** Cada tentativa extra é registrada. Um retry calado
  esconderia exatamente a degradação que ele existe para atravessar, e o repositório já
  pagou caro por esse padrão.

O código vive aqui, e não em cada módulo, porque `_fetch_html` era byte-idêntico nos
dois — e duplicação é o motivo de a mesma correção precisar ser escrita duas vezes.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

#: Quantas tentativas no total, contando a primeira. Três dá ~0,9% de falha residual
#: com a taxa medida (30% de rejeição, 83% resolvidos na tentativa seguinte).
DEFAULT_ATTEMPTS = 3

#: Pausa entre tentativas. 1,5s foi o valor com que a medição foi feita.
DEFAULT_BACKOFF_SECONDS = 1.5

#: Status que melhoram com insistência. 404 fica fora de propósito: o recurso não existe,
#: e repetir só adia a conclusão.
RETRYABLE_STATUS = frozenset({403, 408, 429, 500, 502, 503, 504})


class UpstreamRejected(RuntimeError):
    """Falha que veio do upstream, carregando o status quando houve um."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def worst_case_seconds(timeout_seconds: float, attempts: int, backoff: float) -> float:
    """Teto de tempo de uma busca completa.

    Precisa caber no intervalo de verificação: se o pior caso passar do intervalo, os
    ciclos se sobrepõem e a fila cresce em silêncio.
    """
    attempts = max(int(attempts), 1)
    return attempts * max(timeout_seconds, 0.0) + max(attempts - 1, 0) * max(backoff, 0.0)


def fetch_html(
    url: str,
    *,
    impersonate: str,
    timeout_seconds: float,
    logger: logging.Logger,
    module_id: str,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
) -> str:
    """Busca o corpo, repetindo o que é transitório. Levanta se todas falharem."""
    attempts = max(int(attempts), 1)
    last: Exception = UpstreamRejected("no attempt was made")

    for attempt in range(1, attempts + 1):
        try:
            return _fetch_once(url, impersonate, timeout_seconds)
        except UpstreamRejected as exc:
            last = exc
            retryable = exc.status is None or exc.status in RETRYABLE_STATUS
        except Exception as exc:  # noqa: BLE001
            # Erro de rede: sem status, e tipicamente transitório.
            last = exc
            retryable = True

        if not retryable or attempt == attempts:
            break

        logger.warning(
            "upstream fetch failed; retrying",
            extra={
                "event": "fetch_retry",
                "module_id": module_id,
                "reason": f"attempt {attempt}/{attempts}: {last}",
                "url": url,
            },
        )
        sleep(backoff_seconds)

    raise last


def _fetch_once(url: str, impersonate: str, timeout_seconds: float) -> str:
    # Importado tarde para o resto da aplicacao nao depender de curl_cffi.
    from curl_cffi import requests as cffi_requests

    response = cffi_requests.get(url, impersonate=impersonate, timeout=timeout_seconds)
    if response.status_code >= 400:
        raise UpstreamRejected(
            f"upstream returned HTTP {response.status_code}", response.status_code
        )
    text = response.text
    if not text:
        # Nao e transitorio: 200 com corpo vazio e o upstream respondendo errado, e
        # insistir so atrasa o diagnostico.
        raise UpstreamRejected("upstream returned empty body", 200)
    return text
