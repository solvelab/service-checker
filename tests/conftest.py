import pytest


@pytest.fixture(autouse=True)
def _no_fetch_backoff(monkeypatch):
    """Os retries de busca nao dormem em teste.

    O retry existe porque a borda da Cloudflare recusa ~30% das requisicoes e 83%
    dessas passam na tentativa seguinte. A pausa real e de 1,5s; multiplicada pelos
    casos de falha, a suite ficava dez vezes mais lenta sem testar nada a mais.
    """
    monkeypatch.setenv("STEAM_FETCH_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("ROCKSTAR_FETCH_BACKOFF_SECONDS", "0")
