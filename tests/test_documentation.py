"""Guardas contra a documentação envelhecer sozinha.

Quatro afirmações diziam "nove provedores" depois que o décimo entrou, e o
`openspec/project.md` — o arquivo que um agente lê antes de propor qualquer coisa —
ainda descrevia dois canais de saída e estado só em memória, ambos falsos.

Documentação errada não quebra teste nenhum, e é por isso que ela apodrece. Estes
guardas dão a ela a mesma propriedade que o resto do repositório tem: divergir do código
vira build vermelho.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = ["README.md", "AGENTS.md", "DOCKER.md", "openspec/project.md"]

_NUMERO = {
    "um": 1, "dois": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
}


def _modulos() -> int:
    return len([p for p in (_ROOT / "app" / "modules").iterdir()
                if p.is_dir() and not p.name.startswith("_")])


def _afirmacoes():
    """Toda frase que declara uma quantidade de provedores, módulos ou páginas."""
    padrao = re.compile(
        r"\b(\d+|um|dois|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze)\s+"
        r"(?:\*\*)?(provedores|m[oó]dulos|monitores|p[aá]ginas|providers|modules)",
        re.IGNORECASE,
    )
    for nome in _DOCS:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            for bruto, alvo in padrao.findall(linha):
                valor = _NUMERO.get(bruto.lower(), None)
                if valor is None:
                    valor = int(bruto) if bruto.isdigit() else None
                if valor is not None:
                    yield nome, numero, valor, alvo, linha.strip()[:70]


def test_the_sweep_actually_finds_claims():
    """Guarda do guarda: uma regex quebrada deixaria tudo verde por vacuidade."""
    encontradas = list(_afirmacoes())
    assert len(encontradas) >= 3, "a varredura não achou afirmações — a regex quebrou"


def test_no_document_claims_the_wrong_provider_count():
    total = _modulos()
    erradas = [
        f"{nome}:{linha} diz {valor} {alvo} (são {total}) -> {texto}"
        for nome, linha, valor, alvo, texto in _afirmacoes()
        # Só interessa a contagem de provedores/módulos monitorados. Outras quantidades
        # — dependências, canais, tentativas — têm padrões próprios e não entram aqui.
        if alvo.lower() in {"provedores", "modulos", "módulos", "monitores", "providers", "modules"}
        and valor != total
    ]
    assert erradas == [], "documentação divergindo de app/modules/:\n  " + "\n  ".join(erradas)


@pytest.mark.parametrize("termo", ["Alertmanager", "Google Chat", "cloudflare"])
def test_the_project_context_knows_what_the_project_does(termo):
    """`project.md` é o que um agente lê antes de propor; desatualizado, orienta errado."""
    texto = (_ROOT / "openspec" / "project.md").read_text(encoding="utf-8")
    assert termo.lower() in texto.lower(), f"project.md não menciona {termo}"


def test_the_project_context_does_not_still_claim_memory_only_state():
    """A frase sobreviveu à entrega que a tornou falsa."""
    texto = (_ROOT / "openspec" / "project.md").read_text(encoding="utf-8")
    assert "Estado só em memória" not in texto


def test_every_env_var_of_the_example_is_documented():
    """`DOCKER.md` é a referência de configuração; variável fora dele não existe para quem lê."""
    exemplo = (_ROOT / ".env.example").read_text(encoding="utf-8")
    reais = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", exemplo, re.M))
    documentadas = set(re.findall(r"`([A-Z][A-Z0-9_]+)`",
                                  (_ROOT / "DOCKER.md").read_text(encoding="utf-8")))
    # As por-módulo são descritas por prefixo (`<SLUG>_URL` etc.), não uma a uma.
    prefixos = tuple(f"{p.name.upper()}_" for p in (_ROOT / "app" / "modules").iterdir()
                     if p.is_dir() and not p.name.startswith("_"))
    faltando = sorted(v for v in reais - documentadas if not v.startswith(prefixos))
    assert faltando == [], f"variáveis fora do DOCKER.md: {faltando}"


def test_every_module_has_a_readme():
    faltando = [p.name for p in (_ROOT / "app" / "modules").iterdir()
                if p.is_dir() and not p.name.startswith("_") and not (p / "README.md").exists()]
    assert faltando == []
