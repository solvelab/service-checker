"""O estado de notificação só avança quando alguém recebeu.

`_dispatch` isola os canais e engole a exceção de cada um — correto, e é o que impede um
canal quebrado de silenciar os outros. Mas quem chamava gravava o estado logo depois, sem
perguntar se alguém tinha recebido. Duas consequências, ambas medidas antes de corrigir:

**O alerta se perdia por uma falha de um minuto.**

    minuto 0  canal fora, alerta disparado  -> nao entregue, estado grava "enviado"
    minuto 1  canal volta, degradacao segue -> suprimido pelo throttle
    minuto 2, 3, 5, 8                       -> suprimidos
    entregue em: NUNCA

Dez minutos de `NOTIFICATION_REPEAT_MINUTES` engolidos por uma indisponibilidade de
sessenta segundos. Se a degradação terminasse antes, ninguém jamais teria sabido dela.

**A resolução era anunciada sem o problema.**

    queda total de 5 min, dez modulos:
    durante: 10 tentativas de "monitor com falha", todas falham (canal tambem caiu)
    depois : 10 mensagens de "monitor recuperado"

O sinal invertido: você descobre que houve problema pela mensagem de que ele acabou.

Não é hipótese — este cluster reinicia o roteador todos os dias às 04:00.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ModuleConfig,
    NotificationConfig,
    RuleConfig,
    TelegramConfig,
    WebhookConfig,
)
from app.core.logging import JsonFormatter, configure_logging
from app.core.notifications import NotificationManager
from app.core.types import MonitorResult, MonitorStatus

_T0 = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
MODULES = ["steam", "openai", "claude", "rockstar", "oci",
           "gcp", "aws", "github", "bitbucket", "cloudflare"]


class Channel:
    """Canal que pode cair e voltar, contando o que conseguiu entregar."""

    def __init__(self, up=True):
        self.up = up
        self.delivered: list[tuple[str, str]] = []

    async def _send(self, kind, kwargs):
        if not self.up:
            raise ConnectionError("channel unreachable")
        self.delivered.append((kind, kwargs["event_time"].strftime("%H:%M")))
        return True

    async def send_alert(self, **kwargs):
        return await self._send("alert", kwargs)

    async def send_recovery(self, **kwargs):
        return await self._send("recovery", kwargs)

    async def send_monitor_error(self, **kwargs):
        return await self._send("monitor_error", kwargs)

    async def send_monitor_recovered(self, **kwargs):
        return await self._send("monitor_recovered", kwargs)


def _manager(*channels, threshold=3, repeat=10):
    manager = NotificationManager(
        NotificationConfig(
            telegram=TelegramConfig(False, None, [], "https://api.telegram.org", "%Y", "UTC"),
            webhook=WebhookConfig(False, None, None, "Authorization"),
            repeat_minutes=repeat,
            error_threshold=threshold,
        )
    )
    for index, channel in enumerate(channels):
        manager.register(f"ch{index}", channel)
    return manager


def _config(slug="aws"):
    return ModuleConfig(slug, "https://example.com", 60, 10.0, "ua",
                        RuleConfig("status", "x"), [], True)


async def _feed(manager, status, minute, module_id="aws", payload=None, logger=None):
    reason = "Comp: down" if status == MonitorStatus.ALERT else None
    if status == MonitorStatus.ERROR:
        reason = "ConnectionError: network unreachable"
    await manager.handle_result(
        module_id=module_id,
        result=MonitorResult(status, "m", reason, 1.0, payload),
        module_config=_config(module_id),
        level_name="WARNING",
        event_name="monitor_check",
        event_time=_T0 + timedelta(minutes=minute),
        http_client=AsyncMock(),
        logger=logger or MagicMock(spec=logging.Logger),
    )


def _kinds(channel, kind):
    return [when for k, when in channel.delivered if k == kind]


# ---------------------------------------------------------------------------
# O alerta perdido
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_alert_that_failed_to_deliver_is_retried_next_cycle():
    """O caso decisivo. Antes disto, o alerta nunca chegava."""
    channel = Channel(up=False)
    manager = _manager(channel)

    await _feed(manager, MonitorStatus.ALERT, 0)
    channel.up = True
    await _feed(manager, MonitorStatus.ALERT, 1)

    assert _kinds(channel, "alert") == ["04:01"]


@pytest.mark.asyncio
async def test_a_delivered_alert_is_throttled_as_before():
    """A correção não pode virar spam: entregue uma vez, a janela vale."""
    channel = Channel()
    manager = _manager(channel, repeat=10)

    for minute in range(4):
        await _feed(manager, MonitorStatus.ALERT, minute)

    assert _kinds(channel, "alert") == ["04:00"]


@pytest.mark.asyncio
async def test_the_repeat_still_fires_after_the_window():
    channel = Channel()
    manager = _manager(channel, repeat=10)
    await _feed(manager, MonitorStatus.ALERT, 0)
    await _feed(manager, MonitorStatus.ALERT, 11)
    assert _kinds(channel, "alert") == ["04:00", "04:11"]


@pytest.mark.asyncio
async def test_one_channel_of_two_delivering_is_enough():
    """Isolamento continua valendo: um canal quebrado não trava o estado."""
    broken, healthy = Channel(up=False), Channel()
    manager = _manager(broken, healthy, repeat=10)

    await _feed(manager, MonitorStatus.ALERT, 0)
    await _feed(manager, MonitorStatus.ALERT, 1)

    assert _kinds(healthy, "alert") == ["04:00"]


@pytest.mark.asyncio
async def test_a_long_outage_retries_every_cycle():
    channel = Channel(up=False)
    manager = _manager(channel)
    for minute in range(5):
        await _feed(manager, MonitorStatus.ALERT, minute)
    channel.up = True
    await _feed(manager, MonitorStatus.ALERT, 5)
    assert _kinds(channel, "alert") == ["04:05"]


@pytest.mark.asyncio
async def test_a_per_component_alert_is_also_retried():
    """O ramo por serviço tem o próprio ponto de gravação, e o mesmo defeito."""
    channel = Channel(up=False)
    manager = _manager(channel)
    item = [{"id": "dx", "name": "Direct Connect", "status": "open"}]

    await _feed(manager, MonitorStatus.ALERT, 0, payload=item)
    channel.up = True
    await _feed(manager, MonitorStatus.ALERT, 1, payload=item)

    assert _kinds(channel, "alert") == ["04:01"]


@pytest.mark.asyncio
async def test_one_component_failing_does_not_block_the_others():
    """Dois componentes, canal fora só no primeiro ciclo: os dois saem depois."""
    channel = Channel(up=False)
    manager = _manager(channel)
    items = [{"id": "a", "name": "A", "status": "open"},
             {"id": "b", "name": "B", "status": "open"}]

    await _feed(manager, MonitorStatus.ALERT, 0, payload=items)
    channel.up = True
    await _feed(manager, MonitorStatus.ALERT, 1, payload=items)

    assert len(_kinds(channel, "alert")) == 2


# ---------------------------------------------------------------------------
# A resolução anunciada sem o problema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_undelivered_failure_produces_no_recovery_message():
    channel = Channel(up=False)
    manager = _manager(channel, threshold=1)

    await _feed(manager, MonitorStatus.ERROR, 0)
    channel.up = True
    await _feed(manager, MonitorStatus.OK, 1, payload=[])

    assert _kinds(channel, "monitor_recovered") == []


@pytest.mark.asyncio
async def test_a_delivered_failure_still_produces_its_recovery():
    channel = Channel()
    manager = _manager(channel, threshold=1)

    await _feed(manager, MonitorStatus.ERROR, 0)
    await _feed(manager, MonitorStatus.OK, 1, payload=[])

    assert _kinds(channel, "monitor_error") == ["04:00"]
    assert _kinds(channel, "monitor_recovered") == ["04:01"]


@pytest.mark.asyncio
async def test_a_failure_retried_until_delivered_then_recovers():
    channel = Channel(up=False)
    manager = _manager(channel, threshold=1)

    await _feed(manager, MonitorStatus.ERROR, 0)
    channel.up = True
    await _feed(manager, MonitorStatus.ERROR, 1)
    await _feed(manager, MonitorStatus.OK, 2, payload=[])

    assert _kinds(channel, "monitor_error") == ["04:01"]
    assert _kinds(channel, "monitor_recovered") == ["04:02"]


@pytest.mark.asyncio
async def test_a_blind_window_is_logged_even_though_it_is_not_notified():
    """Silêncio total seria o padrão que este repositório passou o dia caçando."""
    logger = configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    channel = Channel(up=False)
    manager = _manager(channel, threshold=1)
    await _feed(manager, MonitorStatus.ERROR, 0, logger=logger)
    channel.up = True
    await _feed(manager, MonitorStatus.OK, 1, payload=[], logger=logger)

    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    blind = [line for line in lines if line["event"] == "monitor_blind_window"]
    assert len(blind) == 1
    assert "no channel accepted" in blind[0]["reason"]


# ---------------------------------------------------------------------------
# O cenário do roteador: queda total, dez módulos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_total_outage_produces_no_phantom_recoveries():
    """04:00, todo dia: o roteador reinicia e a internet cai por alguns minutos."""
    channel = Channel()
    manager = _manager(channel, threshold=3)

    async def cycle(minute, down):
        channel.up = not down
        for slug in MODULES:
            status = MonitorStatus.ERROR if down else MonitorStatus.OK
            await _feed(manager, status, minute, module_id=slug,
                        payload=None if down else [])

    for minute in range(3):
        await cycle(minute, False)
    for minute in range(3, 8):
        await cycle(minute, True)
    for minute in range(8, 12):
        await cycle(minute, False)

    assert _kinds(channel, "monitor_recovered") == []
    assert _kinds(channel, "monitor_error") == []


@pytest.mark.asyncio
async def test_an_outage_shorter_than_the_threshold_notifies_nothing():
    channel = Channel()
    manager = _manager(channel, threshold=3)
    await _feed(manager, MonitorStatus.ERROR, 0)
    await _feed(manager, MonitorStatus.OK, 1, payload=[])
    assert channel.delivered == []


# ---------------------------------------------------------------------------
# Bug-Hunter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_with_no_channel_registered_the_state_still_advances():
    """Sem canal não há o que entregar; o comportamento antigo tem de valer."""
    manager = _manager(repeat=10)
    await _feed(manager, MonitorStatus.ALERT, 0)
    state = manager._alert_state.get("aws")
    assert state is not None and state.last_alert_at is not None


@pytest.mark.asyncio
async def test_a_channel_registered_after_the_failure_receives_the_retry():
    channel = Channel(up=False)
    manager = _manager(channel)
    await _feed(manager, MonitorStatus.ALERT, 0)

    late = Channel()
    manager.register("late", late)
    await _feed(manager, MonitorStatus.ALERT, 1)

    assert _kinds(late, "alert") == ["04:01"]


@pytest.mark.asyncio
async def test_a_channel_that_fails_only_on_recovery_does_not_lose_the_alert():
    class OnlyRecoveryFails(Channel):
        async def send_recovery(self, **kwargs):
            raise ConnectionError("down")

    channel = OnlyRecoveryFails()
    manager = _manager(channel)
    await _feed(manager, MonitorStatus.ALERT, 0)
    await _feed(manager, MonitorStatus.OK, 1, payload=[])

    assert _kinds(channel, "alert") == ["04:00"]


@pytest.mark.asyncio
async def test_every_channel_failing_leaves_the_state_untouched():
    a, b = Channel(up=False), Channel(up=False)
    manager = _manager(a, b)
    await _feed(manager, MonitorStatus.ALERT, 0)
    assert manager._alert_state.get("aws") is None


@pytest.mark.asyncio
async def test_the_failure_of_each_channel_is_still_logged():
    """A correção não pode custar o log por canal entregue na #8."""
    logger = MagicMock(spec=logging.Logger)
    manager = _manager(Channel(up=False), Channel(up=False))
    await _feed(manager, MonitorStatus.ALERT, 0, logger=logger)
    failures = [c for c in logger.error.call_args_list
                if c[0][0] == "notification channel failed"]
    assert len(failures) == 2
    assert {c[1]["extra"]["target"] for c in failures} == {"ch0", "ch1"}
