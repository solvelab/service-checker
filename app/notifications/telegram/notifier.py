import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...core.types import MonitorResult

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


class TelegramNotifier:
    def __init__(self, config) -> None:
        self.config = config

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
