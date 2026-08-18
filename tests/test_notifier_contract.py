"""Um canal que não entregou precisa dizer que não entregou.

A #71 instalou o portão: `if not delivered: return`, para que o throttle não engula um
alerta que ninguém recebeu. O portão nunca fechou. `_dispatch` inferia a entrega da
ausência de exceção, e o `Notifier` Protocol manda o contrário — *"an HTTP error is
logged and swallowed, never raised"*. Os quatro canais obedeciam. Logo `delivered` era
`True` sempre, e o único caminho que exercitava o mecanismo era o canal deliberadamente
quebrado de `simulate_notifications.py`, que levanta.

Estes testes usam os canais **reais**, com o transporte falhando do jeito que ele falha
na vida: exceção de conexão e resposta 5xx. Contra o código anterior a esta suíte, os
que verificam não-entrega passam a valer — e o `test_the_alert_is_not_lost_...` é o que
prova a consequência que doía: alerta suprimido pelo throttle sem ninguém ter visto.
"""
from __future__ import annotations

import ast
import inspect
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    AlertmanagerConfig,
    GoogleChatConfig,
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus
from app.notifications.alertmanager.notifier import AlertmanagerNotifier
from app.notifications.google_chat.notifier import GoogleChatNotifier
from app.notifications.telegram.notifier import TelegramNotifier
from app.notifications.webhook.notifier import WebhookNotifier

_T0 = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
_SEND_METHODS = (
    "send_alert",
    "send_recovery",
    "send_monitor_error",
    "send_monitor_recovered",
)


def _client(*, raises=None, status=200):
    """An http client whose POST fails the way a real one fails."""
    client = MagicMock()
    if raises is not None:
        client.post = AsyncMock(side_effect=raises)
    else:
        client.post = AsyncMock(return_value=MagicMock(status_code=status, text="body"))
    return client


def _kwargs(client):
    return {
        "module_id": "aws",
        "result": MonitorResult(MonitorStatus.ALERT, "m", "Comp: down", 1.0, None),
        "interval_seconds": 60,
        "level_name": "WARNING",
        "event_name": "service_alert",
        "event_time": _T0,
        "http_client": client,
        "logger": MagicMock(spec=logging.Logger),
    }


def _channels():
    """One live instance of each of the four, configured enough to send."""
    return {
        "webhook": WebhookNotifier(
            WebhookConfig(True, "https://hook.example.com", None, "Authorization")
        ),
        "telegram": TelegramNotifier(
            TelegramConfig(
                True, "token", ["1"], "https://api.telegram.org", "%Y-%m-%d", "UTC"
            )
        ),
        "google_chat": GoogleChatNotifier(
            GoogleChatConfig(True, "https://chat.example.com/x?key=k", 0.0, False)
        ),
        "alertmanager": AlertmanagerNotifier(
            AlertmanagerConfig(True, "http://am:9093", None, "Authorization", 0.0, {})
        ),
    }


# ---------------------------------------------------------------------------
# O canal reporta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_channels()))
@pytest.mark.asyncio
async def test_a_transport_failure_is_reported_as_not_delivered(name):
    channel = _channels()[name]
    delivered = await channel.send_alert(
        **_kwargs(_client(raises=ConnectionError("network unreachable")))
    )
    assert delivered is False


@pytest.mark.parametrize("name", sorted(_channels()))
@pytest.mark.asyncio
async def test_a_rejected_request_is_reported_as_not_delivered(name):
    """5xx é não-entrega. O webhook nem olhava o status: 500 era indistinguível de 200."""
    channel = _channels()[name]
    delivered = await channel.send_alert(**_kwargs(_client(status=500)))
    assert delivered is False


@pytest.mark.parametrize("name", sorted(_channels()))
@pytest.mark.asyncio
async def test_a_successful_send_is_reported_as_delivered(name):
    channel = _channels()[name]
    delivered = await channel.send_alert(**_kwargs(_client(status=200)))
    assert delivered is True


@pytest.mark.parametrize("method", _SEND_METHODS)
@pytest.mark.parametrize("name", sorted(_channels()))
@pytest.mark.asyncio
async def test_every_event_kind_reports_delivery(name, method):
    """Os quatro eventos, não só o alerta: a recuperação some do mesmo jeito."""
    channel = _channels()[name]
    assert await getattr(channel, method)(**_kwargs(_client(status=200))) is True
    assert await getattr(channel, method)(**_kwargs(_client(status=503))) is False


@pytest.mark.asyncio
async def test_one_chat_of_two_accepting_counts_as_delivered():
    """Alguém leu. Reenviar duplicaria a mensagem para quem já viu."""
    channel = TelegramNotifier(
        TelegramConfig(
            True, "token", ["1", "2"], "https://api.telegram.org", "%Y-%m-%d", "UTC"
        )
    )
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[MagicMock(status_code=500, text="no"), MagicMock(status_code=200)]
    )
    assert await channel.send_alert(**_kwargs(client)) is True


@pytest.mark.asyncio
async def test_no_chat_accepting_is_not_delivered():
    channel = TelegramNotifier(
        TelegramConfig(
            True, "token", ["1", "2"], "https://api.telegram.org", "%Y-%m-%d", "UTC"
        )
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=500, text="no"))
    assert await channel.send_alert(**_kwargs(client)) is False


@pytest.mark.parametrize("name", sorted(_channels()))
@pytest.mark.asyncio
async def test_a_channel_never_raises_at_the_caller(name):
    """O isolamento continua sendo responsabilidade do canal, não de quem chama."""
    channel = _channels()[name]
    await channel.send_alert(**_kwargs(_client(raises=RuntimeError("boom"))))


# ---------------------------------------------------------------------------
# A consequência: o estado
# ---------------------------------------------------------------------------

def _manager(*channels, repeat=10):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(
                False, None, [], "https://api.telegram.org", "%Y", "UTC"
            ),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=repeat,
            error_threshold=3,
        )
    )
    for index, channel in enumerate(channels):
        manager.register(f"ch{index}", channel)
    return manager


async def _feed(manager, client, minute, status=MonitorStatus.ALERT):
    await manager.handle_result(
        module_id="aws",
        result=MonitorResult(
            status, "m", "Comp: down" if status == MonitorStatus.ALERT else None, 1.0
        ),
        module_config=ModuleConfig(
            "aws", "https://example.com", 60, 10.0, "ua", RuleConfig("status", "x"), [], True
        ),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=client,
        logger=MagicMock(spec=logging.Logger),
    )


@pytest.mark.asyncio
async def test_the_alert_is_not_lost_when_the_only_channel_swallows_the_failure():
    """O caso que doía, com o canal real em vez de um que levanta.

    Minuto 0: o endpoint responde 500. O canal loga e engole — por contrato. Antes,
    `delivered` virava `True` mesmo assim, o estado avançava, e os dez minutos de
    `NOTIFICATION_REPEAT_MINUTES` suprimiam toda repetição. A degradação podia começar
    e terminar sem uma única mensagem entregue.
    """
    channel = WebhookNotifier(
        WebhookConfig(True, "https://hook.example.com", None, "Authorization")
    )
    manager = _manager(channel, repeat=10)

    down = _client(status=500)
    await _feed(manager, down, 0)
    assert down.post.await_count == 1

    up = _client(status=200)
    await _feed(manager, up, 1)
    assert up.post.await_count == 1, "o ciclo seguinte precisa reenviar"


@pytest.mark.asyncio
async def test_a_delivered_alert_is_still_throttled():
    """A correção não pode virar reenvio a cada ciclo."""
    channel = WebhookNotifier(
        WebhookConfig(True, "https://hook.example.com", None, "Authorization")
    )
    manager = _manager(channel, repeat=10)

    first = _client(status=200)
    await _feed(manager, first, 0)
    assert first.post.await_count == 1

    second = _client(status=200)
    await _feed(manager, second, 1)
    assert second.post.await_count == 0


@pytest.mark.asyncio
async def test_one_channel_delivering_is_enough_for_the_state_to_advance():
    broken = WebhookNotifier(
        WebhookConfig(True, "https://broken.example.com", None, "Authorization")
    )
    healthy = WebhookNotifier(
        WebhookConfig(True, "https://ok.example.com", None, "Authorization")
    )
    manager = _manager(broken, healthy)

    # Um cliente só, compartilhado: o primeiro POST falha, o segundo passa.
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[MagicMock(status_code=500, text="no"), MagicMock(status_code=200)]
    )
    await _feed(manager, client, 0)

    later = _client(status=200)
    await _feed(manager, later, 1)
    assert later.post.await_count == 0, "alguem recebeu; o throttle vale"


@pytest.mark.asyncio
async def test_a_channel_outside_the_contract_does_not_count_as_delivered():
    """`None` não é aceite. Contá-lo como entrega é o defeito antigo com outra roupa."""

    class Mute:
        async def send_alert(self, **kwargs):
            return None

        async def send_recovery(self, **kwargs):
            return None

        async def send_monitor_error(self, **kwargs):
            return None

        async def send_monitor_recovered(self, **kwargs):
            return None

    manager = _manager(Mute())
    logger = MagicMock(spec=logging.Logger)
    await manager.handle_result(
        module_id="aws",
        result=MonitorResult(MonitorStatus.ALERT, "m", "Comp: down", 1.0),
        module_config=ModuleConfig(
            "aws", "https://example.com", 60, 10.0, "ua", RuleConfig("status", "x"), [], True
        ),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0,
        http_client=AsyncMock(),
        logger=logger,
    )
    assert manager._alert_state == {}, "sem aceite, o estado nao avanca"
    assert any(
        "contract" in str(call.args[0]) for call in logger.error.call_args_list
    ), "a violacao de contrato precisa de sinal, nao de silencio"


# ---------------------------------------------------------------------------
# O guarda: um canal novo não pode reabrir o vão
# ---------------------------------------------------------------------------

def _notifier_modules():
    root = Path(__file__).resolve().parent.parent / "app" / "notifications"
    return sorted(p for p in root.glob("*/notifier.py"))


def test_the_sweep_finds_every_channel():
    """Guarda do guarda: um glob quebrado deixaria o teste abaixo verde por vacuidade."""
    assert len(_notifier_modules()) == 4, [p.name for p in _notifier_modules()]


@pytest.mark.parametrize("path", _notifier_modules(), ids=lambda p: p.parent.name)
def test_every_channel_declares_the_delivery_contract(path):
    """Assinatura anotada como `bool`, para o contrato ser legível sem rodar nada."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert classes, path
    found = {}
    for cls in classes:
        for node in cls.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in _SEND_METHODS:
                found[node.name] = ast.unparse(node.returns) if node.returns else None
    missing = sorted(set(_SEND_METHODS) - set(found))
    assert not missing, f"{path.parent.name} nao implementa {missing}"
    wrong = {name: ann for name, ann in found.items() if ann != "bool"}
    assert not wrong, f"{path.parent.name} fora do contrato: {wrong}"


def test_the_protocol_declares_the_delivery_contract():
    """`app/core/types.py` é onde o contrato vive; se ele afrouxar, tudo afrouxa."""
    from app.core.types import Notifier

    for method in _SEND_METHODS:
        hints = inspect.get_annotations(getattr(Notifier, method), eval_str=False)
        assert hints.get("return") == "bool", method
