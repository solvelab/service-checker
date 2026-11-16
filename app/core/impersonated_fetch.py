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

**Remedido em 2026-11-15, e a premissa mudou.** Nos logs de produção (`v2.8.9`, 814
checagens do `steam` entre 10:07 e 00:10 UTC, `chrome124`):

    1ª tentativa recusada          515 de 814  -> 63% (era 30%)
    2ª tentativa recusada de novo  421 de 515  -> 82% (antes, 17%)
    3ª tentativa recusada de novo  366 de 421  -> 87%
    checagem terminando em ERROR   366 de 814  -> 45%

A recusa deixou de ser transitória: uma vez recusado, o retry com 1,5s quase sempre é
recusado de novo, e as sequências de ERROR duram de 1 a 20 ciclos. O retry continua
certo para o caso transitório, mas não resolve este. O que contém o ruído no canal é a
histerese de recuperação do `NotificationManager` (issue #87); a causa — perfil de
imitação, IP de saída do cluster ou regra nova da borda — segue sem diagnóstico, porque
testar outros perfis contra o site não foi autorizado na sessão que mediu.

O código vive aqui, e não em cada módulo, porque `_fetch_html` era byte-idêntico nos
dois — e duplicação é o motivo de a mesma correção precisar ser escrita duas vezes.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

#: Quantas tentativas no total, contando a primeira. Três dava ~0,9% de falha residual
#: com a taxa medida em 2026-08 (30% de rejeição, 83% resolvidos na tentativa seguinte);
#: com a de 2026-11-15 a falha residual medida é 45% — ver o docstring do módulo.
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


class UpstreamChallenged(UpstreamRejected):
    """A borda desafiou o cliente automatizado (issue #94).

    Diagnosticado na #87: o 403 do `steamstat.us` é o Cloudflare Bot Management,
    `cf-mitigated: challenge` com a página "Just a moment...". É política do operador
    da fonte, não falha do checker, da rede ou do provedor — e o motivo precisa dizer
    isso, ou o operador investiga no lugar errado. A repetição segue o status, como em
    qualquer recusa: um desafio com 403, 429 ou 5xx de `RETRYABLE_STATUS` é repetido
    (parte passa na tentativa seguinte); com 401, 404 ou 52x, não.
    """


#: Cabeçalho com que o Cloudflare marca uma resposta de desafio (observado 2026-11-16).
_CHALLENGE_HEADER = "cf-mitigated"
_CHALLENGE_VALUE = "challenge"

#: Teto do texto de exceção no log de `challenge_check_failed`: a mensagem pode trazer
#: bytes do cabeçalho, que vêm do upstream.
_REASON_LIMIT = 200


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
            return _fetch_once(url, impersonate, timeout_seconds, logger, module_id)
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


def _fetch_once(
    url: str,
    impersonate: str,
    timeout_seconds: float,
    logger: logging.Logger,
    module_id: str,
) -> str:
    # Importado tarde para o resto da aplicacao nao depender de curl_cffi.
    from curl_cffi import requests as cffi_requests

    response = cffi_requests.get(url, impersonate=impersonate, timeout=timeout_seconds)
    if response.status_code >= 400 and _is_challenge(response, logger, module_id, url):
        raise UpstreamChallenged(
            "upstream challenged the automated client "
            f"(Cloudflare {_CHALLENGE_HEADER}: {_CHALLENGE_VALUE}, "
            f"HTTP {response.status_code})",
            response.status_code,
        )
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


def _is_challenge(response, logger: logging.Logger, module_id: str, url: str) -> bool:
    """True quando algum `cf-mitigated` traz o valor `challenge`, sem diferenciar maiúsculas.

    Igualdade por item, não substring: o Cloudflare documenta só o valor `challenge`, e o
    motivo afirma esse valor — `non-challenge` não pode virar desafio. O `curl_cffi` junta
    cabeçalhos repetidos com ", ", por isso a lista é separada antes de comparar.

    A detecção só acrescenta informação ao motivo (issue #98): se a leitura dos cabeçalhos
    falhar, a recusa segue como `upstream returned HTTP <status>`, com o status e a
    repetição de sempre, e a falha vai para o log em vez de virar erro de rede.
    """
    try:
        items = getattr(getattr(response, "headers", None), "items", None)
        if not callable(items):
            return False
        for name, value in items():
            if str(name).lower() != _CHALLENGE_HEADER:
                continue
            tokens = str(value).split(",")
            if any(token.strip().lower() == _CHALLENGE_VALUE for token in tokens):
                return True
        return False
    except Exception as header_error:  # noqa: BLE001
        # Nome próprio na cláusula: a allowlist de test_silent_fallbacks casa pelo texto,
        # e "except Exception as exc" já está liberado para o loop de fetch_html.
        logger.warning(
            "could not read response headers to check for a challenge",
            extra={
                "event": "challenge_check_failed",
                "module_id": module_id,
                "status": getattr(response, "status_code", None),
                "reason": f"{type(header_error).__name__}: {header_error}"[:_REASON_LIMIT],
                "url": url,
            },
        )
        return False
