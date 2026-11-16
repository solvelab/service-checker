import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...core.types import MonitorResult
from .summary import KIND_MONITORING, KIND_SERVICE, DailySummary

_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(enabled_extensions=("j2", "html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)
_DEFAULT_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_alert.j2")
_STEAM_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_steam.j2")
_RESOLVED_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_resolved.j2")
_MONITOR_ERROR_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_monitor_error.j2")
_MONITOR_RECOVERED_TEMPLATE = _TEMPLATE_ENV.get_template("telegram_monitor_recovered.j2")


#: Um problema novo dentro desta janela depois de um reenvio edita em vez de reenviar:
#: o toque acabou de acontecer, e N componentes de um incidente não viram N toques.
#: lean: janela fixa -> configurável se 60s se mostrar curto ou longo.
_RESEND_COOLDOWN_SECONDS = 60
#: Única recusa de edição que significa sucesso: a mensagem já mostra este texto.
_NOT_MODIFIED = "message is not modified"


class TelegramNotifier:
    def __init__(self, config, max_age_minutes: int = 1440) -> None:
        self.config = config
        self._summary: Optional[DailySummary] = None
        if getattr(config, "mode", "per_event") == "daily_summary":
            self._summary = DailySummary(
                _summary_zone(getattr(config, "summary_timezone", "UTC")),
                timedelta(minutes=max(int(max_age_minutes), 0)),
            )
        # Os módulos rodam em tarefas concorrentes; modelo e mensagem do dia são
        # compartilhados, então cada evento aplica e escreve inteiro antes do próximo.
        self._lock = asyncio.Lock()
        # Último texto escrito em cada chat: uma repetição que não muda nada não gasta
        # chamada à Bot API (o limite de edição é por chat).
        self._last_text: dict[str, str] = {}
        # Chats cujo reenvio com toque falhou: o próximo escrito neles toca.
        self._ring_owed: set[str] = set()

    # -- daily summary state (issue #100) -------------------------------------

    def export_state(self) -> Optional[dict]:
        return self._summary.to_dict() if self._summary is not None else None

    def restore_state(self, data) -> None:
        if self._summary is None or data is None:
            return
        self._summary.restore(
            data,
            _utcnow(),
            logging.getLogger("service_monitor.telegram"),
        )

    async def send_alert(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if self._summary is not None:
            return await self._summarize(
                "alert", module_id, result, event_time, http_client, logger
            )
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return False

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_payload(module_id, payload, logger)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        # Sucesso parcial conta: se um chat recebeu, alguem viu, e reenviar no
        # ciclo seguinte duplicaria a mensagem para quem ja leu.
        delivered = False
        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )
        return delivered

    async def send_recovery(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if self._summary is not None:
            return await self._summarize(
                "recovery", module_id, result, event_time, http_client, logger
            )
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return False

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_with_template(_RESOLVED_TEMPLATE, payload, logger, module_id)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        # Sucesso parcial conta: se um chat recebeu, alguem viu, e reenviar no
        # ciclo seguinte duplicaria a mensagem para quem ja leu.
        delivered = False
        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )
        return delivered

    async def send_monitor_error(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if self._summary is not None:
            return await self._summarize(
                "monitor_error", module_id, result, event_time, http_client, logger
            )
        return await self._send_with_template(
            _MONITOR_ERROR_TEMPLATE,
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )

    async def send_monitor_recovered(
        self,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if self._summary is not None:
            return await self._summarize(
                "monitor_recovered", module_id, result, event_time, http_client, logger
            )
        return await self._send_with_template(
            _MONITOR_RECOVERED_TEMPLATE,
            module_id,
            result,
            interval_seconds,
            level_name,
            event_name,
            event_time,
            http_client,
            logger,
        )

    async def _send_with_template(
        self,
        template,
        module_id: str,
        result: MonitorResult,
        interval_seconds: int,
        level_name: str,
        event_name: str,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={
                    "event": "notify_skip",
                    "module_id": module_id,
                    "target": "telegram",
                },
            )
            return False

        payload = _build_payload(
            module_id,
            result,
            interval_seconds,
            self.config.timestamp_format,
            self.config.timestamp_zone,
            level_name,
            event_name,
            event_time,
        )
        text = _render_with_template(template, payload, logger, module_id)
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"

        # Sucesso parcial conta: se um chat recebeu, alguem viu, e reenviar no
        # ciclo seguinte duplicaria a mensagem para quem ja leu.
        delivered = False
        for chat_id in self.config.chat_ids:
            try:
                response = await http_client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        "telegram notification rejected",
                        extra={
                            "event": "notify_error",
                            "module_id": module_id,
                            "target": "telegram",
                            "chat_id": chat_id,
                            "reason": f"status {response.status_code}: {response.text}",
                        },
                    )
                    continue
                logger.info(
                    "telegram notification sent",
                    extra={
                        "event": "notify",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                    },
                )
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram notification failed",
                    extra={
                        "event": "notify_error",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": str(exc),
                    },
                )
        return delivered

    async def _summarize(
        self,
        event: str,
        module_id: str,
        result: MonitorResult,
        event_time: datetime,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        """Apply one event to the day's summary and bring every chat up to date."""
        if not self.config.bot_token or not self.config.chat_ids:
            logger.warning(
                "telegram notifier missing token or chat_ids; skipping",
                extra={"event": "notify_skip", "module_id": module_id, "target": "telegram"},
            )
            return False

        now = event_time if event_time.tzinfo else event_time.replace(tzinfo=timezone.utc)
        key, name, kind, status = _problem_identity(event, module_id, result)
        async with self._lock:
            summary = self._summary
            summary.roll(now)
            if event in ("alert", "monitor_error"):
                summary.open_problem(
                    key, module_id, name, kind, status, now,
                    started_at=_reported_start(result, now),
                )
            else:
                summary.resolve(key, module_id, name, kind, now)
            return await self._sync(now, module_id, http_client, logger)

    async def flush(self, http_client: httpx.AsyncClient, logger: logging.Logger) -> None:
        """Retry whatever a chat is missing; called by the manager after every cycle.

        The manager does not retry a service recovery (its state is dropped whatever the
        channels answer), and a quiet day has no next event to carry the fix. Without
        this, a recovery edit refused with 429 would leave the problem showing open for
        the rest of the day.
        """
        summary = self._summary
        if summary is None or not self.config.bot_token or not self.config.chat_ids:
            return
        if summary.day is None or not (summary.entries or summary.message_ids):
            return
        async with self._lock:
            await self._sync(_utcnow(), "telegram", http_client, logger, only_stale=True)

    async def _sync(
        self,
        now: datetime,
        module_id: str,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
        only_stale: bool = False,
    ) -> bool:
        """Make every chat show the current summary; ring where a problem is still owed.

        A ring is owed while some problem has not rung anywhere (`has_unnotified`), and
        per chat while that chat's ringing resend failed. Any write may pay the debt —
        the retry of the same alert, another module's event, or `flush` — so a ring that
        failed once is never downgraded to a silent edit by whatever happens next.
        """
        summary = self._summary
        text = summary.render()
        owes = summary.has_unnotified()
        cooling = (
            summary.last_resend_at is not None
            and 0 <= (now - summary.last_resend_at).total_seconds() < _RESEND_COOLDOWN_SECONDS
        )
        delivered = rang = False
        for raw_chat in self.config.chat_ids:
            chat_id = str(raw_chat)
            ring = (owes and not cooling) or chat_id in self._ring_owed
            current = (
                chat_id in summary.message_ids and self._last_text.get(chat_id) == text
            )
            if not ring and current:
                delivered = True
                continue
            if only_stale and not ring and chat_id not in self._last_text:
                # Depois de um restart o cache está vazio; isso não é falha a refazer.
                continue
            if await self._write_summary(chat_id, text, ring, module_id, http_client, logger):
                delivered = True
                rang = rang or ring
                self._ring_owed.discard(chat_id)
            elif ring:
                self._ring_owed.add(chat_id)
        if rang:
            summary.mark_notified()
            summary.last_resend_at = now
        elif delivered and owes and cooling:
            # Coalescido com o toque de segundos atrás: aparece na mensagem que tocou.
            summary.mark_notified()
        return delivered

    async def _write_summary(
        self,
        chat_id: str,
        text: str,
        resend: bool,
        module_id: str,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> bool:
        message_id = self._summary.message_ids.get(chat_id)

        if message_id is not None and not resend:
            status, description, _ = await self._bot_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                http_client,
            )
            if status is not None and status < 400:
                self._last_text[chat_id] = text
                _log_summary(logger, module_id, chat_id, "summary edited")
                return True
            if status == 400 and _NOT_MODIFIED in description.lower():
                self._last_text[chat_id] = text
                return True
            if status != 400:
                # Limite de taxa, 5xx ou rede: transitório. Mandar outra mensagem aqui
                # duplicaria o resumo; o manager tenta de novo no ciclo seguinte.
                _log_summary_error(logger, module_id, chat_id, "editMessageText", status, description)
                return False
            # 400: a mensagem sumiu ou não pode mais ser editada. Segue para uma nova.
            logger.warning(
                "telegram summary could not be edited; sending a new message",
                extra={
                    "event": "notify_fallback",
                    "module_id": module_id,
                    "target": "telegram",
                    "chat_id": chat_id,
                    "reason": f"status {status}: {description}",
                },
            )
        elif message_id is not None:
            status, description, _ = await self._bot_call(
                "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, http_client
            )
            if status is not None and status < 400:
                # Apagada: o id não serve mais, mesmo que o envio abaixo falhe.
                self._summary.message_ids.pop(chat_id, None)
                self._last_text.pop(chat_id, None)
            else:
                # Sobra a mensagem velha junto da nova: feio, mas o problema novo chega.
                logger.warning(
                    "telegram summary could not be deleted before the resend",
                    extra={
                        "event": "notify_fallback",
                        "module_id": module_id,
                        "target": "telegram",
                        "chat_id": chat_id,
                        "reason": f"status {status}: {description}",
                    },
                )

        status, description, new_id = await self._bot_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": not resend,
            },
            http_client,
        )
        if status is None or status >= 400:
            _log_summary_error(logger, module_id, chat_id, "sendMessage", status, description)
            return False
        if type(new_id) is int:
            self._summary.message_ids[chat_id] = new_id
        else:
            self._summary.message_ids.pop(chat_id, None)
            logger.warning(
                "telegram did not return a message_id; the next event sends a new message",
                extra={
                    "event": "notify_fallback",
                    "module_id": module_id,
                    "target": "telegram",
                    "chat_id": chat_id,
                    "reason": description or "no message_id in the response",
                },
            )
        self._last_text[chat_id] = text
        _log_summary(logger, module_id, chat_id, "summary resent" if resend else "summary sent")
        return True

    async def _bot_call(self, method: str, body: dict, http_client: httpx.AsyncClient):
        """Call the Bot API; return `(status or None, description, message_id or None)`."""
        url = f"{self.config.api_url.rstrip('/')}/bot{self.config.bot_token}/{method}"
        try:
            response = await http_client.post(url, json=body, timeout=10.0)
        except Exception as exc:  # noqa: BLE001 - signalled by status None
            return None, f"{type(exc).__name__}: {exc}", None
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - not JSON: judged by status alone
            data = None
        if not isinstance(data, dict):
            # Sem JSON a resposta ainda é julgada pelo status; o texto vai para o log.
            return response.status_code, str(getattr(response, "text", "") or ""), None
        result = data.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return response.status_code, str(data.get("description") or ""), message_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _summary_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("service_monitor.telegram").warning(
            "summary timezone is not a known IANA zone; using UTC",
            extra={"event": "config_fallback", "target": "telegram",
                   "reason": f"{name!r}: {type(exc).__name__}"},
        )
        return ZoneInfo("UTC")


def _reported_start(result: MonitorResult, now: datetime) -> datetime:
    """The start a problem reports for itself (the checker's connectivity loss, #106).

    Never later than `now` and never unreadable: anything else is simply `now`.
    """
    payload = result.payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        raw = payload[0].get("started_at")
        if isinstance(raw, str):
            try:
                started = datetime.fromisoformat(raw)
            except ValueError:  # unreadable hint: the event time is the start
                return now
            started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
            return min(started, now)
    return now


def _problem_identity(event: str, module_id: str, result: MonitorResult):
    """`(key, name, kind, status)` of the problem an event is about.

    Components use the same identity as the manager's alert state (`_service_key`), so
    a recovery closes exactly the entry its alert opened. Monitoring failures live in a
    namespace of their own (no ":"), so no component key can collide with them.
    """
    if event in ("monitor_error", "monitor_recovered"):
        reason = result.reason or result.message or ""
        _, marker, last = reason.partition("last error: ")
        # Sem ":" de propósito: toda chave de componente tem "module:id", e a de módulo
        # é o slug, então nenhuma das duas coincide com esta.
        key = f"monitor-error/{module_id}"
        return key, module_id, KIND_MONITORING, (last if marker else reason)

    payload = result.payload
    if isinstance(payload, list) and payload and all(isinstance(i, dict) for i in payload):
        # Importado tarde: `core.notifications` importa este módulo.
        from ...core.notifications import _service_key

        # A recuperação chega com `from_status`/`to_status` acrescentados; tirá-los mantém
        # a chave igual à do alerta também quando ela é o digest do conteúdo.
        item = {
            k: v for k, v in payload[0].items() if k not in ("from_status", "to_status")
        }
        name = str(item.get("name") or item.get("id") or module_id)
        status = str(
            item.get("status_text") or item.get("status") or item.get("severity") or ""
        )
        return _service_key(module_id, item), name, KIND_SERVICE, status
    return module_id.lower(), module_id, KIND_SERVICE, result.reason or result.message or ""


def _log_summary(logger: logging.Logger, module_id: str, chat_id: str, what: str) -> None:
    logger.info(
        f"telegram {what}",
        extra={"event": "notify", "module_id": module_id, "target": "telegram",
               "chat_id": chat_id},
    )


def _log_summary_error(logger, module_id, chat_id, method, status, description) -> None:
    logger.error(
        "telegram summary write failed",
        extra={
            "event": "notify_error",
            "module_id": module_id,
            "target": "telegram",
            "chat_id": chat_id,
            "reason": f"{method}: status {status}: {description}",
        },
    )


def _build_payload(
    module_id: str,
    result: MonitorResult,
    interval_seconds: int,
    timestamp_format: str,
    timestamp_zone: str,
    level_name: str,
    event_name: str,
    event_time: datetime,
) -> dict:
    timestamp = _format_timestamp(event_time, timestamp_format, timestamp_zone)
    reason = result.reason or result.message or "sem detalhes"
    reason_items = _reason_items(result, reason)
    duration_ms = f"{result.duration_ms:.2f}" if result.duration_ms is not None else "0.00"
    message = result.message or "sem detalhes"
    return {
        "timestamp": timestamp,
        "level": level_name,
        "message": message,
        "event": event_name,
        "module_id": module_id,
        "status": result.status.value,
        "reason": reason,
        "reason_items": reason_items,
        "services": _serialize_services(result.payload),
        "duration_ms": duration_ms,
        "interval_seconds": interval_seconds,
    }


def _reason_items(result: MonitorResult, reason: str) -> List[str]:
    """One bullet per incident.

    The monitor already knows where one incident ends and the next begins, so it
    hands the list over on `MonitorResult.reason_items`. Re-splitting the joined
    sentence is the fallback and it is lossy by nature: every candidate separator
    appears inside some provider's content — OCI titles carry "|", GitHub incident
    titles carry "," and ";".
    """
    if result.reason_items:
        return [item.strip() for item in result.reason_items if item and item.strip()]
    return _split_reason(reason)


def _split_reason(reason: str) -> List[str]:
    parts = [item.strip() for item in reason.split(",")]
    return [item for item in parts if item]


def _serialize_services(payload) -> List[dict]:
    services: List[dict] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            services.append(
                {
                    "name": item.get("name") or item.get("id") or "service",
                    "status_text": item.get("status_text") or item.get("class") or "",
                    "severity": (item.get("severity") or "unknown").upper(),
                    "id": item.get("id") or "",
                    "slug": item.get("slug") or "",
                    "status": item.get("status") or "",
                    "from_status": item.get("from_status") or "",
                    "to_status": item.get("to_status") or "",
                }
            )
    return services


def _select_template(module_id: str):
    return _STEAM_TEMPLATE if module_id.lower() == "steam" else _DEFAULT_TEMPLATE


def _render_payload(module_id: str, payload: dict, logger: logging.Logger) -> str:
    return _render_with_template(_select_template(module_id), payload, logger, module_id)


def _render_with_template(
    template, payload: dict, logger: logging.Logger, module_id: str
) -> str:
    try:
        return template.render(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "telegram template render failed; using fallback",
            extra={
                "event": "notify_error",
                "module_id": module_id,
                "target": "telegram",
                "reason": str(exc),
            },
        )
        fallback = _DEFAULT_TEMPLATE
        try:
            return fallback.render(payload)
        except Exception as fallback_exc:  # noqa: BLE001
            # O alerta ainda sai, mas so com o essencial. Sem esta linha, a diferenca
            # entre "template customizado renderizou" e "sobrou o minimo" nao aparece
            # em lugar nenhum — e a mensagem degradada e plausivel o bastante para
            # ninguem estranhar.
            logger.error(
                "telegram fallback template also failed; sending a bare message",
                extra={
                    "event": "notify_error",
                    "module_id": module_id,
                    "target": "telegram",
                    "reason": str(fallback_exc),
                },
            )
            return f"{module_id} alert: {payload.get('message', 'no details')}"


def _format_timestamp(dt: datetime, fmt: str, zone: str) -> str:
    zone_upper = (zone or "UTC").strip().upper()
    if zone_upper == "LOCAL":
        target = dt.astimezone()
    else:
        target = dt.astimezone(timezone.utc)
    # Filho de `service_monitor` de proposito: `configure_logging` so monta aquela
    # arvore, e com `propagate = False` — um logger de fora nao tem handler nenhum.
    log = logging.getLogger("service_monitor.telegram")
    try:
        formatted = target.strftime(fmt)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "timestamp format could not be applied; using ISO-8601",
            extra={"event": "config_fallback", "target": "telegram",
                   "reason": f"{fmt!r}: {exc}"},
        )
        return target.isoformat()

    # O `except` acima quase nunca dispara: no Linux, `strftime` com uma diretiva
    # desconhecida nao levanta — devolve o texto literal. Entao um
    # `TELEGRAM_TIMESTAMP_FORMAT` errado nao caia no fallback, ele **emitia o lixo**
    # dentro do alerta, e ninguem ficava sabendo. Se nenhuma diretiva foi interpretada,
    # o formato nao vale nada.
    if "%" in fmt and formatted == fmt:
        log.warning(
            "timestamp format has no directive the platform understands; using ISO-8601",
            extra={"event": "config_fallback", "target": "telegram", "reason": repr(fmt)},
        )
        return target.isoformat()
    return formatted
