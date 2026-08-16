"""Tests para a busca com impersonação e retry.

O `steam` alternava 403 e OK em produção. Medido na vigília do cluster e reproduzido
daqui:

    sequencia (X=403): X..X.X.............XXX........   ~18% dos ciclos
    duracao OK  278ms   duracao 403  129ms
    20 requisicoes diretas: {200: 14, 403: 6}
    retry apos 1,5s resolveu 5 de 6

A rejeição chega **mais rápido** que o sucesso: é a borda recusando na hora, não o
upstream sofrendo. Com verificação a cada 60s, três recusas seguidas acontecem sozinhas
e disparam a notificação de monitor morto — o operador recebe um incidente que não
existe, e aprende a ignorar o canal.

O que estes testes fixam, além do óbvio:

- **404 não é repetido.** Insistir num recurso que não existe só atrasa o diagnóstico.
- **A repetição não é silenciosa.** Um retry calado esconderia justamente a degradação
  que ele atravessa, e este repositório já pagou caro por esse padrão.
- **O pior caso cabe no intervalo.** Se três tentativas com pausa passarem dos 60s do
  ciclo, as verificações se sobrepõem e a fila cresce sem ninguém ver.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from app.core.impersonated_fetch import (
    DEFAULT_ATTEMPTS,
    DEFAULT_BACKOFF_SECONDS,
    RETRYABLE_STATUS,
    UpstreamRejected,
    fetch_html,
    worst_case_seconds,
)
from app.core.logging import JsonFormatter, configure_logging


class _Sleep:
    """Substitui `time.sleep`, registrando as pausas em vez de dormi-las."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _responses(*items):
    """Sequência de respostas: um int vira status, uma str vira corpo 200."""
    queue = list(items)

    class Response:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    def get(url, **kwargs):
        item = queue.pop(0) if queue else 200
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return Response(item, "" if item >= 400 else "<html>ok</html>")
        return Response(200, item)

    return get


def _fetch(monkeypatch, *items, attempts=DEFAULT_ATTEMPTS, logger=None):
    monkeypatch.setattr("curl_cffi.requests.get", _responses(*items))
    sleep = _Sleep()
    body = fetch_html(
        "https://example.com/status",
        impersonate="chrome124",
        timeout_seconds=10.0,
        logger=logger or logging.getLogger("test"),
        module_id="steam",
        attempts=attempts,
        backoff_seconds=0.0,
        sleep=sleep,
    )
    return body, sleep


# ---------------------------------------------------------------------------
# O defeito: 403 transitório
# ---------------------------------------------------------------------------

def test_a_403_followed_by_success_is_a_success(monkeypatch):
    """O caso medido: 83% dos 403 somem na tentativa seguinte."""
    body, sleep = _fetch(monkeypatch, 403, "<html>page</html>")
    assert body == "<html>page</html>"
    assert len(sleep.calls) == 1


def test_two_failures_then_success_still_succeeds(monkeypatch):
    body, sleep = _fetch(monkeypatch, 403, 403, "<html>page</html>")
    assert body == "<html>page</html>"
    assert len(sleep.calls) == 2


def test_every_attempt_failing_raises_with_the_last_reason(monkeypatch):
    with pytest.raises(UpstreamRejected, match="HTTP 429"):
        _fetch(monkeypatch, 403, 403, 429)


def test_the_number_of_attempts_is_honoured(monkeypatch):
    _, sleep = _fetch(monkeypatch, 403, 403, 403, 403, "<html>ok</html>", attempts=5)
    assert len(sleep.calls) == 4


def test_a_single_attempt_means_no_retry(monkeypatch):
    with pytest.raises(UpstreamRejected):
        _fetch(monkeypatch, 403, "<html>ok</html>", attempts=1)


def test_zero_attempts_is_treated_as_one(monkeypatch):
    """Configuração absurda não pode virar zero requisição e um OK falso."""
    with pytest.raises(UpstreamRejected):
        _fetch(monkeypatch, 403, attempts=0)


# ---------------------------------------------------------------------------
# O que NÃO é repetido
# ---------------------------------------------------------------------------

def test_a_404_is_not_retried(monkeypatch):
    """O recurso não existe; insistir só atrasa a conclusão.

    A segunda resposta da fila é um sucesso: se houvesse retry, a busca teria terminado
    com `<html>ok</html>` em vez de levantar. É isso que torna o teste conclusivo, e não
    só a contagem de pausas.
    """
    monkeypatch.setattr("curl_cffi.requests.get", _responses(404, "<html>ok</html>"))
    sleep = _Sleep()
    with pytest.raises(UpstreamRejected, match="HTTP 404"):
        fetch_html("https://x/", impersonate="chrome124", timeout_seconds=1.0,
                   logger=logging.getLogger("t"), module_id="steam",
                   backoff_seconds=0.0, sleep=sleep)
    assert sleep.calls == []


def test_a_404_raises_immediately(monkeypatch):
    with pytest.raises(UpstreamRejected, match="HTTP 404"):
        _fetch(monkeypatch, 404)


def test_an_empty_body_is_not_retried(monkeypatch):
    """200 com corpo vazio é o upstream respondendo errado, não instabilidade."""
    monkeypatch.setattr("curl_cffi.requests.get", _responses(""))
    sleep = _Sleep()
    with pytest.raises(UpstreamRejected, match="empty body"):
        fetch_html("https://x/", impersonate="chrome124", timeout_seconds=1.0,
                   logger=logging.getLogger("t"), module_id="steam",
                   backoff_seconds=0.0, sleep=sleep)
    assert sleep.calls == []


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
def test_every_retryable_status_is_retried(monkeypatch, status):
    _, sleep = _fetch(monkeypatch, status, "<html>ok</html>")
    assert len(sleep.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 404, 410, 418])
def test_a_permanent_status_is_not_retried(monkeypatch, status):
    monkeypatch.setattr("curl_cffi.requests.get", _responses(status))
    sleep = _Sleep()
    with pytest.raises(UpstreamRejected):
        fetch_html("https://x/", impersonate="chrome124", timeout_seconds=1.0,
                   logger=logging.getLogger("t"), module_id="steam",
                   backoff_seconds=0.0, sleep=sleep)
    assert sleep.calls == []


def test_a_network_error_is_retried(monkeypatch):
    """Sem status: tipicamente transitório, e é o caso que mais justifica repetir."""
    body, sleep = _fetch(monkeypatch, ConnectionError("reset by peer"), "<html>ok</html>")
    assert body == "<html>ok</html>"
    assert len(sleep.calls) == 1


def test_a_network_error_on_the_last_attempt_propagates(monkeypatch):
    with pytest.raises(ConnectionError):
        _fetch(monkeypatch, 403, 403, ConnectionError("reset by peer"))


# ---------------------------------------------------------------------------
# A repetição não é silenciosa
# ---------------------------------------------------------------------------

def test_a_retry_is_logged(monkeypatch):
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    _fetch(monkeypatch, 403, "<html>ok</html>", logger=logger)

    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    retries = [line for line in lines if line["event"] == "fetch_retry"]
    assert len(retries) == 1
    assert retries[0]["module_id"] == "steam"
    assert "attempt 1/3" in retries[0]["reason"]
    assert "403" in retries[0]["reason"]


def test_a_success_on_the_first_attempt_logs_nothing(monkeypatch):
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    _fetch(monkeypatch, "<html>ok</html>", logger=logger)

    assert stream.getvalue().strip() == ""


# ---------------------------------------------------------------------------
# Orçamento de tempo
# ---------------------------------------------------------------------------

def test_the_worst_case_fits_the_check_interval():
    """Se o pior caso passar do intervalo, os ciclos se sobrepõem em silêncio."""
    for timeout in (10.0, 15.0):
        budget = worst_case_seconds(timeout, DEFAULT_ATTEMPTS, DEFAULT_BACKOFF_SECONDS)
        assert budget < 60.0, (timeout, budget)


def test_the_worst_case_formula_counts_attempts_and_pauses():
    assert worst_case_seconds(10.0, 3, 1.5) == pytest.approx(33.0)
    assert worst_case_seconds(10.0, 1, 1.5) == pytest.approx(10.0)


def test_a_timeout_longer_than_the_interval_is_visible_in_the_budget():
    """Não é impedido aqui — mas o cálculo o expõe, em vez de escondê-lo."""
    assert worst_case_seconds(30.0, 3, 1.5) > 60.0


# ---------------------------------------------------------------------------
# Os módulos usam o helper compartilhado
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug", ["steam", "rockstar"])
def test_the_impersonating_modules_share_the_implementation(slug):
    """Cópia própria reintroduz o defeito junto — foi assim na #54."""
    import importlib

    module = importlib.import_module(f"app.modules.{slug}.monitor")
    assert module.fetch_html is fetch_html


@pytest.mark.parametrize("slug", ["steam", "rockstar"])
def test_an_invalid_attempts_setting_falls_back_and_says_so(monkeypatch, slug, capsys):
    import importlib

    configure_logging("INFO")
    monkeypatch.setenv(f"{slug.upper()}_FETCH_ATTEMPTS", "tres")
    module = importlib.import_module(f"app.modules.{slug}.monitor")
    monitor = module.get_monitor(slug)
    assert monitor._attempts == DEFAULT_ATTEMPTS
    out = capsys.readouterr().out
    assert "fetch attempts is not a whole number" in out


@pytest.mark.parametrize("slug", ["steam", "rockstar"])
def test_attempts_can_be_turned_down_to_one(monkeypatch, slug):
    import importlib

    monkeypatch.setenv(f"{slug.upper()}_FETCH_ATTEMPTS", "1")
    module = importlib.import_module(f"app.modules.{slug}.monitor")
    assert module.get_monitor(slug)._attempts == 1
