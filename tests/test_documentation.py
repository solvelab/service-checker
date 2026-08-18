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


# ---------------------------------------------------------------------------
# O guarda de contagem acima só via `<número> <substantivo>` colado. Estas são as
# classes que passaram por baixo dele, cada uma achada errada no HEAD 8891d5a.
# ---------------------------------------------------------------------------

# A tabela original só tem português, e as afirmações erradas do README estavam em
# inglês ("the nine real providers"). Um guarda que não sabe ler o número da frase que
# ele varre é um guarda que passa por vacuidade.
_NUMERO_EN = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _valor(bruto: str):
    chave = bruto.lower()
    if chave in _NUMERO:
        return _NUMERO[chave]
    if chave in _NUMERO_EN:
        return _NUMERO_EN[chave]
    return int(bruto) if bruto.isdigit() else None


def _canais() -> list:
    return sorted(p.name for p in (_ROOT / "app" / "notifications").iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def _specs() -> list:
    return sorted(p.parent.name for p in (_ROOT / "openspec" / "specs").glob("*/spec.md"))


def _afirmacoes_com_adjetivo():
    """`the nine real providers` — o adjetivo no meio fazia a regex original não casar."""
    padrao = re.compile(
        r"\b(\d+|um|dois|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze"
        r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?:[a-zà-ú]+\s+){1,2}"
        r"(provedores|m[oó]dulos|monitores|providers|modules)\b",
        re.IGNORECASE,
    )
    for nome in _DOCS + ["scripts/simulate_alerts.py", "scripts/simulate_endpoints.py",
                         "scripts/simulate_notifications.py"]:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            for bruto, alvo in padrao.findall(linha):
                valor = _valor(bruto)
                if valor is not None:
                    yield nome, numero, valor, alvo, linha.strip()[:70]


def test_the_adjective_sweep_actually_finds_claims():
    """Guarda do guarda, com o caso real: regex E tabela de números precisam funcionar.

    A primeira versão deste guarda só testava a regex, e passava enquanto a varredura
    descartava `nine` por não estar na tabela de números — verde por vacuidade, que é
    exatamente o defeito que ele existe para impedir.
    """
    padrao = re.compile(
        r"\b(\d+|nine|ten)\s+(?:[a-z]+\s+){1,2}(providers|modules)\b", re.IGNORECASE
    )
    achado = padrao.findall("this queries nine real providers, so it is a diagnostic")
    assert achado, "a regex de adjetivo parou de casar o caso que a motivou"
    assert _valor(achado[0][0]) == 9, "a varredura não sabe ler o número que acabou de casar"


def test_no_document_hides_a_wrong_count_behind_an_adjective():
    total = _modulos()
    erradas = [f"{nome}:{linha} diz {valor} {alvo} (são {total}) -> {texto}"
               for nome, linha, valor, alvo, texto in _afirmacoes_com_adjetivo()
               if valor != total]
    assert erradas == [], "contagem errada com adjetivo no meio:\n  " + "\n  ".join(erradas)


def test_the_flow_diagram_counts_match_the_directories():
    """O diagrama ASCII do README carrega dois números que nenhuma regex de prosa vê."""
    linhas = [ln for ln in (_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
              if "state + throttle" in ln]
    assert len(linhas) == 1, "a linha do diagrama de fluxo sumiu ou duplicou"
    numeros = [int(n) for n in re.findall(r"\((\d+)\)", linhas[0])]
    assert numeros == [_modulos(), len(_canais())], \
        f"diagrama diz {numeros}, diretórios dizem {[_modulos(), len(_canais())]}"


def test_no_document_claims_the_wrong_channel_count():
    padrao = re.compile(
        r"\b(\d+|um|dois|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez"
        r"|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:\*\*)?(?:[a-zà-ú]+\s+){0,2}(canais|channels)\b",
        re.IGNORECASE,
    )
    total = len(_canais())
    erradas = []
    for nome in _DOCS:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            for bruto, _alvo in padrao.findall(linha):
                valor = _valor(bruto)
                if valor is not None and valor != total:
                    erradas.append(f"{nome}:{numero} diz {valor} canais (são {total})")
    assert erradas == [], "\n  ".join(erradas)


@pytest.mark.parametrize("doc", ["README.md", "DOCKER.md"])
def test_the_reference_docs_name_every_channel(doc):
    """`DOCKER.md` dizia 'Telegram or Webhook' meses depois do terceiro e do quarto entrarem."""
    texto = (_ROOT / doc).read_text(encoding="utf-8").lower()
    apelido = {"google_chat": "google chat"}
    faltando = [c for c in _canais() if apelido.get(c, c) not in texto]
    assert faltando == [], f"{doc} não menciona: {faltando}"


def test_no_document_claims_the_wrong_spec_count():
    padrao = re.compile(
        r"\b(\d+|um|dois|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze"
        r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?:[a-zà-ú]+\s+){0,2}(specs|especifica[cç][oõ]es)\b",
        re.IGNORECASE,
    )
    total = len(_specs())
    erradas = []
    for nome in _DOCS:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            for bruto, _alvo in padrao.findall(linha):
                valor = _valor(bruto)
                # "one spec per capability" é distributivo, não uma contagem.
                if valor is not None and valor > 1 and valor != total:
                    erradas.append(f"{nome}:{numero} diz {valor} specs (são {total})")
    assert erradas == [], "\n  ".join(erradas)


def test_every_badge_points_at_a_workflow_that_exists():
    """Dois badges apontavam para release.yml e publish.yml; só existe ci.yml."""
    quebrados = []
    for nome in _DOCS:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        texto = caminho.read_text(encoding="utf-8")
        for arquivo in re.findall(r"actions/workflows/([A-Za-z0-9_.-]+)/badge\.svg", texto):
            if not (_ROOT / ".github" / "workflows" / arquivo).exists():
                quebrados.append(f"{nome} aponta para .github/workflows/{arquivo}")
    assert quebrados == [], "\n  ".join(quebrados)


def _links_relativos():
    docs = _DOCS + ["CHANGELOG.md"]
    docs += [str(p.relative_to(_ROOT)) for p in (_ROOT / "app").glob("*/*/README.md")]
    for nome in docs:
        caminho = _ROOT / nome
        if not caminho.exists():
            continue
        texto = caminho.read_text(encoding="utf-8")
        for alvo in re.findall(r"\]\(([^)]+)\)", texto):
            alvo = alvo.split("#", 1)[0].strip()
            if not alvo or alvo.startswith(("http://", "https://", "mailto:")):
                continue
            yield nome, alvo, (caminho.parent / alvo).resolve()


def test_the_link_sweep_actually_finds_links():
    assert len(list(_links_relativos())) >= 20, "a varredura de links quebrou"


def test_every_relative_link_resolves():
    """`CHANGELOG.md` mandava o leitor a `docs/DOCKER.md`, caminho que nunca existiu."""
    quebrados = [f"{nome} -> {alvo}" for nome, alvo, destino in _links_relativos()
                 if not destino.exists()]
    assert quebrados == [], "\n  ".join(quebrados)


def test_documented_defaults_match_the_code():
    """`DOCKER.md` dizia que o default do carimbo era sem `%Z`; o código põe `%Z`."""
    docker = (_ROOT / "DOCKER.md").read_text(encoding="utf-8")
    fonte = "\n".join(p.read_text(encoding="utf-8")
                      for p in (_ROOT / "app").rglob("*.py"))
    no_codigo = dict(re.findall(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"\s*,\s*"([^"]*)"\s*\)', fonte))
    divergentes = []
    for var, documentado in re.findall(
        r"`([A-Z][A-Z0-9_]+)`\s*\(default\s*`([^`]*)`\)", docker
    ):
        real = no_codigo.get(var)
        if real is not None and real != documentado:
            divergentes.append(f"{var}: DOCKER.md diz {documentado!r}, código usa {real!r}")
    assert divergentes == [], "\n  ".join(divergentes)


def test_the_default_sweep_actually_finds_defaults():
    docker = (_ROOT / "DOCKER.md").read_text(encoding="utf-8")
    achados = re.findall(r"`([A-Z][A-Z0-9_]+)`\s*\(default\s*`([^`]*)`\)", docker)
    assert len(achados) >= 5, "a varredura de defaults do DOCKER.md quebrou"


def test_the_example_manifest_carries_every_env_var():
    """Ele se anuncia completo, e um manifesto k8s não tem `env_file` para compensar."""
    exemplo = (_ROOT / ".env.example").read_text(encoding="utf-8")
    manifesto = (_ROOT / "deployment.example.yaml").read_text(encoding="utf-8")
    reais = sorted(set(re.findall(r"^([A-Z][A-Z0-9_]+)=", exemplo, re.M)))
    faltando = [v for v in reais if v not in manifesto]
    assert faltando == [], f"ausentes de deployment.example.yaml: {faltando}"


def test_the_env_example_uses_the_project_name():
    """`service-monitor/` é o nome antigo; o default do código é `service-checker/`."""
    exemplo = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "service-monitor/" not in exemplo


def test_every_channel_has_a_readme():
    faltando = [c for c in _canais()
                if not (_ROOT / "app" / "notifications" / c / "README.md").exists()]
    assert faltando == []
