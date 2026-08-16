"""O detector de fallback silencioso, e os sete que ele encontrou.

Uma sequência inteira de defeitos neste repositório teve a mesma forma: o caminho de
degradação funcionava, e nada dizia que ele havia sido tomado. Canais desligados por
quatro meses, `aws` e `gcp` alertando sem all-clear, `cfx` inexistente no manifesto,
payload malformado capturado pelo scheduler sem virar `MonitorStatus.ERROR`, `target`
descartado do log, o `StateStore` logando numa árvore sem handler, o release que
pararia de bumpar a tag imprimindo "not found, skipping".

**O fallback funciona, o sinal não.** Todos foram encontrados por acaso — em produção,
ou caçando outra coisa. Nenhum foi encontrado procurando.

Este arquivo tem duas metades, e a segunda é a que dura:

- os testes de cada silêncio corrigido, que provam que agora há voz;
- `test_no_new_silent_fallback`, que varre `app/` e falha quando aparece um `except`
  novo que nem loga, nem re-levanta, nem devolve `MonitorStatus.ERROR`.

Sinalizar pelo **valor de retorno** conta: 21 dos 47 blocos devolvem
`MonitorStatus.ERROR` com `reason`, e esse é o contrato correto deles. Um detector que
exigisse `logger.` de todo mundo produziria 21 falsos positivos e seria desligado na
primeira semana — que é como um detector morre.
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

import pytest

from app.core.logging import JsonFormatter, configure_logging

_APP = Path(__file__).resolve().parent.parent / "app"


def _capture(level="INFO"):
    logger = configure_logging(level)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    def read():
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    return read


# ---------------------------------------------------------------------------
# O detector
# ---------------------------------------------------------------------------

#: Blocos `except` que podem ficar calados, com o motivo de cada um. Uma allowlist sem
#: motivo escrito vira carimbo, e um carimbo não protege de nada.
ACCEPTED_SILENT = {
    # `asyncio.CancelledError` é o desligamento pedido, não uma falha. Logar no shutdown
    # só produz ruído no momento em que ninguém está lendo.
    ("core/scheduler.py", "except asyncio.CancelledError:"),
    # Re-levanta depois de limpar o arquivo temporário; quem chama é que registra.
    ("core/state_store.py", "except Exception:"),
    # `_parse_dt` sinaliza pelo callback `on_reject`, e é `load()` quem registra —
    # com a chave do estado, que a função pura não conhece. Encontrado pelo próprio
    # detector: sinal por callback é invisível para uma varredura textual, e forçar um
    # log aqui produziria a mensagem sem o contexto que a torna útil.
    ("core/state_store.py", "except ValueError:"),
}


def _except_blocks():
    """Todo `except` de `app/`, com o corpo e como ele sinaliza."""
    for path in sorted(_APP.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not re.match(r"\s*except\b", line):
                continue
            indent = len(line) - len(line.lstrip())
            body = []
            for following in lines[index + 1:]:
                if following.strip() and (len(following) - len(following.lstrip())) <= indent:
                    break
                body.append(following)
            text = "\n".join(body)
            yield {
                "file": str(path.relative_to(_APP.parent)).replace("app/", ""),
                "line": index + 1,
                "clause": line.strip(),
                "logs": bool(re.search(r"(logger|log|self\._logger)\.(debug|info|warning|error|exception)", text))
                or "_warn" in text,
                "raises": bool(re.search(r"\braise\b", text)),
                "returns_error": "MonitorStatus.ERROR" in text,
            }


def test_the_detector_actually_finds_blocks():
    """Guarda do guarda: uma regex quebrada deixaria a varredura verde por vacuidade."""
    blocks = list(_except_blocks())
    assert len(blocks) > 30
    assert any(b["logs"] for b in blocks)
    assert any(b["returns_error"] for b in blocks)


def test_no_new_silent_fallback():
    """Um `except` que degrada sem dizer nada é o padrão que este repositório sangrou."""
    silent = [
        b
        for b in _except_blocks()
        if not (b["logs"] or b["raises"] or b["returns_error"])
        and (b["file"], b["clause"]) not in ACCEPTED_SILENT
    ]
    assert silent == [], (
        "estes blocos degradam sem sinal nenhum — logue o que aconteceu, ou "
        "acrescente à ACCEPTED_SILENT com o motivo escrito:\n"
        + "\n".join(f"  {b['file']}:{b['line']}  {b['clause']}" for b in silent)
    )


def test_the_allowlist_has_no_dead_entries():
    """Uma allowlist que envelhece perde a autoridade que a torna útil."""
    present = {(b["file"], b["clause"]) for b in _except_blocks()}
    stale = sorted(entry for entry in ACCEPTED_SILENT if entry not in present)
    assert stale == [], f"na allowlist mas já não existem: {stale}"


def test_returning_an_error_result_counts_as_signalling():
    """O contrato dos módulos é o valor de retorno, não uma chamada de logger."""
    blocks = [b for b in _except_blocks() if b["file"].startswith("modules/")]
    assert any(b["returns_error"] and not b["logs"] for b in blocks)


# ---------------------------------------------------------------------------
# Os sete que ele encontrou
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    return monkeypatch


def test_a_non_numeric_int_is_reported(env):
    from app.core.config import _get_int, drain_config_warnings

    drain_config_warnings()
    env.setenv("X_INT", "6O")
    assert _get_int("X_INT", 60) == 60
    warnings = drain_config_warnings()
    assert len(warnings) == 1
    assert "X_INT" in warnings[0] and "'6O'" in warnings[0] and "60" in warnings[0]


def test_a_non_numeric_float_is_reported(env):
    from app.core.config import _get_float, drain_config_warnings

    drain_config_warnings()
    env.setenv("X_FLOAT", "1O.5")
    assert _get_float("X_FLOAT", 10.0) == 10.0
    assert len(drain_config_warnings()) == 1


def test_an_absent_variable_is_not_reported(env):
    """Ausência é configuração, não erro. Ruído de boot treina gente a não ler log."""
    from app.core.config import _get_int, drain_config_warnings

    drain_config_warnings()
    env.delenv("X_ABSENT", raising=False)
    assert _get_int("X_ABSENT", 60) == 60
    assert drain_config_warnings() == []


def test_an_empty_variable_is_not_reported(env):
    from app.core.config import _get_int, drain_config_warnings

    drain_config_warnings()
    env.setenv("X_EMPTY", "   ")
    assert _get_int("X_EMPTY", 60) == 60
    assert drain_config_warnings() == []


def test_a_valid_value_is_not_reported(env):
    from app.core.config import _get_int, drain_config_warnings

    drain_config_warnings()
    for value in ("0", "-5", "  42  ", "1000000"):
        env.setenv("X_OK", value)
        _get_int("X_OK", 60)
    assert drain_config_warnings() == []


def test_an_oversized_filter_entry_is_reported(env):
    """`csv.Error` real: campo acima do limite de 131072 caracteres."""
    from app.core.config import _get_service_filter, drain_config_warnings

    drain_config_warnings()
    env.setenv("X_FILTER", '"' + "x" * 200_000)
    _get_service_filter("X_FILTER")
    warnings = drain_config_warnings()
    assert len(warnings) == 1
    assert "plain comma split" in warnings[0]


def test_the_drain_empties_the_buffer(env):
    from app.core.config import _get_int, drain_config_warnings

    drain_config_warnings()
    env.setenv("X_INT", "nope")
    _get_int("X_INT", 1)
    assert len(drain_config_warnings()) == 1
    assert drain_config_warnings() == []


def test_an_unknown_log_level_is_reported(capsys):
    """Lido do stdout, que é para onde o handler de produção escreve.

    Não dá para espiar com um handler próprio: o aviso nasce **dentro** de
    `configure_logging`, que recria os handlers e descartaria o espião antes da linha
    sair. Ler o stdout testa o caminho real, e não um arranjo do teste.
    """
    configure_logging("DEBUGG")
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    fallbacks = [line for line in lines if line.get("event") == "config_fallback"]
    assert len(fallbacks) == 1
    assert "DEBUGG" in fallbacks[0]["message"]
    assert fallbacks[0]["level"] == "WARNING"


def test_a_known_log_level_is_not_reported(capsys):
    configure_logging("warning")
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    assert [line for line in lines if line.get("event") == "config_fallback"] == []


def test_a_lowercase_level_is_accepted(capsys):
    """`debug` minúsculo é válido; avisar aqui seria ruído."""
    configure_logging("debug")
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    assert [line for line in lines if line.get("event") == "config_fallback"] == []


def test_an_unreadable_timestamp_in_state_is_named(tmp_path):
    """Antes, `load()` contava o descarte sem dizer o quê nem por quê."""
    from app.core.state_store import SCHEMA_VERSION, StateStore

    read = _capture()
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": SCHEMA_VERSION, "alerts": {"aws:a": {"last_alert_at": "ontem"}}}),
        encoding="utf-8",
    )
    StateStore(str(path)).load()
    lines = [line for line in read() if line["event"] == "state_error"]
    assert len(lines) == 1
    assert "aws:a" in lines[0]["message"]


def test_a_timestamp_format_with_no_usable_directive_is_reported():
    """No Linux `strftime` não levanta com diretiva desconhecida — devolve o literal.

    Então o `except` quase nunca disparava, e um formato errado não caía no fallback:
    ele **emitia o lixo** dentro do alerta.
    """
    from datetime import datetime, timezone

    from app.notifications.telegram.notifier import _format_timestamp

    read = _capture()
    when = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
    assert _format_timestamp(when, "%Q-invalido", "UTC") == when.isoformat()
    assert [line for line in read() if line["event"] == "config_fallback"]


def test_a_valid_timestamp_format_is_applied_quietly():
    from datetime import datetime, timezone

    from app.notifications.telegram.notifier import _format_timestamp

    read = _capture()
    when = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
    assert _format_timestamp(when, "%d/%m/%Y", "UTC") == "16/08/2026"
    assert read() == []


def test_a_literal_format_without_directives_is_left_alone():
    """`hoje` não tem `%`: é um literal que o operador quis, não um formato quebrado."""
    from datetime import datetime, timezone

    from app.notifications.telegram.notifier import _format_timestamp

    read = _capture()
    when = datetime(2026, 8, 16, tzinfo=timezone.utc)
    assert _format_timestamp(when, "hoje", "UTC") == "hoje"
    assert read() == []
