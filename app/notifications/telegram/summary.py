"""O resumo diário do Telegram (issue #100): uma mensagem por chat por dia.

Uma mensagem nova por evento afogava o grupo — um alerta aberto reenviado a cada dez
minutos são ~144 cartões por dia para um problema que não mudou —, e quem recebe passa a
ignorar o chat. Aqui o dia vira um modelo: uma `SummaryEntry` por problema, com início,
último sinal e resolução, e o `message_id` da mensagem do dia em cada chat. O notificador
aplica cada evento ao modelo e reescreve a mensagem inteira.

Toda atualização é idempotente pela chave do problema, porque o `NotificationManager`
reenvia o mesmo evento no ciclo seguinte quando o canal informa que não entregou.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

#: Limite de texto de uma mensagem da Bot API.
MESSAGE_LIMIT = 4096
#: Resolvidos mostrados por serviço quando o resumo completo não cabe.
_COMPACT_RESOLVED = 3

KIND_SERVICE = "service"
KIND_MONITORING = "monitoring"

_TEMPLATE = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(enabled_extensions=("j2", "html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
).get_template("telegram_daily_summary.j2")


@dataclass
class SummaryEntry:
    """Um problema do dia."""

    key: str
    module_id: str
    name: str
    kind: str
    status_text: str
    last_seen_at: datetime
    # None quando o problema começou antes de existir resumo (por exemplo, um alerta
    # restaurado do modo por evento) e só a resolução chegou até aqui.
    started_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    # Se a notificação do problema já tocou em algum chat. Uma escrita que falha deixa
    # False, e o reenvio do manager no ciclo seguinte ainda conta como problema novo.
    notified: bool = False

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


class DailySummary:
    def __init__(self, zone: ZoneInfo, max_age: timedelta) -> None:
        self.zone = zone
        self.max_age = max_age
        self.day: Optional[date] = None
        self.entries: list[SummaryEntry] = []
        self.message_ids: dict[str, int] = {}
        self.last_resend_at: Optional[datetime] = None

    # -- events ---------------------------------------------------------------

    def roll(self, now: datetime) -> bool:
        """Start a new day when `now` is past the current one. True if it rolled.

        Resolved problems stay with the day they belong to. Open ones are carried, but
        only those some event confirmed within `max_age`: the manager forgets state that
        old, so no recovery would ever arrive to close them.
        """
        today = now.astimezone(self.zone).date()
        if self.day is not None and today <= self.day:
            # Igual: mesmo dia. Anterior: um evento com carimbo atrasado (tirado antes
            # de esperar o lock) — voltar o dia abandonaria a mensagem de hoje.
            return False
        # max_age 0 é "sem limite" para o StateStore; aqui viraria carregar para sempre
        # um problema que nenhum evento confirma mais.
        window = self.max_age or timedelta(days=1)
        self.entries = [
            entry
            for entry in self.entries
            if entry.is_open and now - entry.last_seen_at <= window
        ]
        self.day = today
        self.message_ids = {}
        return True

    def open_problem(
        self,
        key: str,
        module_id: str,
        name: str,
        kind: str,
        status_text: str,
        now: datetime,
    ) -> bool:
        """Record a problem; True when it still owes a notification."""
        entry = self._open_entry(key)
        if entry is None:
            entry = SummaryEntry(
                key=key,
                module_id=module_id,
                name=name,
                kind=kind,
                status_text=status_text,
                started_at=now,
                last_seen_at=now,
            )
            self.entries.append(entry)
            return True
        entry.status_text = status_text or entry.status_text
        entry.name = name or entry.name
        entry.last_seen_at = max(entry.last_seen_at, now)
        return not entry.notified

    def resolve(
        self, key: str, module_id: str, name: str, kind: str, now: datetime
    ) -> bool:
        """Close a problem; True when the summary changed."""
        entry = self._open_entry(key)
        if entry is None:
            if any(e.key == key for e in self.entries):
                return False  # Já resolvido: o manager repetiu o evento.
            self.entries.append(
                SummaryEntry(
                    key=key,
                    module_id=module_id,
                    name=name,
                    kind=kind,
                    status_text="",
                    last_seen_at=now,
                    resolved_at=now,
                    notified=True,
                )
            )
            return True
        entry.resolved_at = now
        entry.last_seen_at = max(entry.last_seen_at, now)
        return True

    def has_unnotified(self) -> bool:
        return any(entry.is_open and not entry.notified for entry in self.entries)

    def mark_notified(self) -> None:
        for entry in self.entries:
            entry.notified = True

    def _open_entry(self, key: str) -> Optional[SummaryEntry]:
        for entry in self.entries:
            if entry.key == key and entry.is_open:
                return entry
        return None

    # -- persistence ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat() if self.day else None,
            "message_ids": dict(self.message_ids),
            "last_resend_at": _iso(self.last_resend_at),
            "entries": [
                {
                    "key": e.key,
                    "module_id": e.module_id,
                    "name": e.name,
                    "kind": e.kind,
                    "status_text": e.status_text,
                    "started_at": _iso(e.started_at),
                    "last_seen_at": _iso(e.last_seen_at),
                    "resolved_at": _iso(e.resolved_at),
                    "notified": e.notified,
                }
                for e in self.entries
            ],
        }

    def restore(self, data, now: datetime, logger: logging.Logger) -> None:
        """Load a persisted day. Bad parts are dropped and logged, never raised."""
        if not isinstance(data, dict):
            return
        dropped = 0
        try:
            day = date.fromisoformat(data["day"]) if data.get("day") else None
        except (TypeError, ValueError):  # counted in `dropped`, logged below
            day, dropped = None, dropped + 1
        if day is not None and day > now.astimezone(self.zone).date():
            day, dropped = None, dropped + 1  # Dia do futuro: relógio errado.

        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            dropped += raw_entries is not None
            raw_entries = []
        entries: list[SummaryEntry] = []
        for raw in raw_entries:
            entry = _entry_from_dict(raw)
            if entry is None:
                dropped += 1
                continue
            entries.append(entry)

        raw_ids = data.get("message_ids")
        if not isinstance(raw_ids, dict):
            dropped += raw_ids is not None
            raw_ids = {}
        message_ids = {str(chat): mid for chat, mid in raw_ids.items() if type(mid) is int}
        dropped += len(raw_ids) - len(message_ids)

        self.day = day
        self.entries = entries if day is not None else []
        self.message_ids = message_ids if day is not None else {}
        self.last_resend_at = _parse(data.get("last_resend_at"))
        if data.get("last_resend_at") is not None and self.last_resend_at is None:
            dropped += 1
        logger.info(
            "telegram daily summary restored",
            extra={
                "event": "state_load",
                "target": "telegram",
                "reason": (
                    f"day {self.day}, {len(self.entries)} entry(ies), "
                    f"{len(self.message_ids)} message(s), {dropped} part(s) discarded"
                ),
            },
        )

    # -- rendering ------------------------------------------------------------

    def render(self) -> str:
        """The day's message, compacted until it fits in one Telegram message."""
        for limit in (None, _COMPACT_RESOLVED, 0):
            text = _TEMPLATE.render(self._view(limit)).strip()
            if telegram_length(text) <= MESSAGE_LIMIT:
                return text
        # Só os abertos já não cabem: corta em quebras de linha, onde nenhuma tag fica
        # aberta (cada linha do template fecha as próprias). O rodapé conta o que saiu,
        # para que um problema novo escondido ainda mude o texto.
        lines = text.split("\n")
        total = len(lines)
        while lines:
            footer = f"… (+{total - len(lines)} linha(s) omitidas)"
            candidate = "\n".join(lines + [footer])
            if telegram_length(candidate) <= MESSAGE_LIMIT and len(lines) < total:
                return candidate
            lines.pop()
        return "…"

    def _view(self, resolved_limit: Optional[int]) -> dict:
        services: dict[str, dict] = {}
        monitoring: list[dict] = []
        for entry in sorted(self.entries, key=_sort_key):
            line = {
                "name": entry.name,
                "status": entry.status_text,
                "since": self._when(entry.started_at),
                "until": self._when(entry.resolved_at),
                "duration": _duration(entry.started_at, entry.resolved_at),
            }
            if entry.kind == KIND_MONITORING:
                monitoring.append({**line, "module": entry.module_id, "open": entry.is_open})
                continue
            section = services.setdefault(
                entry.module_id,
                {"module": entry.module_id.upper(), "open": [], "resolved": [], "count": 0},
            )
            section["count"] += 1
            section["open" if entry.is_open else "resolved"].append(line)

        for section in services.values():
            resolved = section["resolved"]
            keep = len(resolved) if resolved_limit is None else resolved_limit
            section["hidden"] = len(resolved) - min(keep, len(resolved))
            section["resolved"] = resolved[len(resolved) - min(keep, len(resolved)):]

        resolved_monitoring = [m for m in monitoring if not m["open"]]
        keep = len(resolved_monitoring) if resolved_limit is None else resolved_limit
        kept = resolved_monitoring[len(resolved_monitoring) - min(keep, len(resolved_monitoring)):]
        monitoring_hidden = len(resolved_monitoring) - len(kept)
        monitoring = [m for m in monitoring if m["open"] or any(m is k for k in kept)]

        open_total = sum(len(s["open"]) for s in services.values()) + sum(
            1 for m in monitoring if m["open"]
        )
        return {
            "day": self.day.strftime("%d/%m/%Y") if self.day else "",
            "services": sorted(services.values(), key=lambda s: (not s["open"], s["module"])),
            "monitoring": monitoring,
            "monitoring_hidden": monitoring_hidden,
            "open_total": open_total,
            "problem_total": len(self.entries),
        }

    def _when(self, value: Optional[datetime]) -> str:
        if value is None:
            return ""
        local = value.astimezone(self.zone)
        if self.day is not None and local.date() != self.day:
            return local.strftime("%d/%m %H:%M")
        return local.strftime("%H:%M")


def telegram_length(text: str) -> int:
    """Length as the Bot API limits it: UTF-16 code units, counted with the markup.

    Python counts code points, and every 🔴/✅ is two UTF-16 units. Counting the tags too
    overestimates on purpose — over the limit, the edit and its fallback send would both
    be refused and the channel would stay dead for the day.
    """
    return len(text.encode("utf-16-le")) // 2


def _sort_key(entry: SummaryEntry):
    first = entry.started_at or entry.resolved_at or entry.last_seen_at
    return (first, entry.key)


def _duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None or end is None:
        return ""
    minutes = max(int((end - start).total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}h{minutes % 60:02d}"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:  # None: callers drop and count a present-but-bad value
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _entry_from_dict(raw) -> Optional[SummaryEntry]:
    if not isinstance(raw, dict):
        return None
    key, module_id, kind = raw.get("key"), raw.get("module_id"), raw.get("kind")
    last_seen = _parse(raw.get("last_seen_at"))
    if not (isinstance(key, str) and isinstance(module_id, str) and last_seen):
        return None
    started, resolved = _parse(raw.get("started_at")), _parse(raw.get("resolved_at"))
    # Ausente é legítimo (início antes do resumo; problema aberto). Presente e ilegível
    # não: um `resolved_at` corrompido viraria um problema aberto carregado dia após dia.
    if (raw.get("started_at") is not None and started is None) or (
        raw.get("resolved_at") is not None and resolved is None
    ):
        return None
    if kind not in (KIND_SERVICE, KIND_MONITORING):
        return None
    return SummaryEntry(
        key=key,
        module_id=module_id,
        name=str(raw.get("name") or module_id),
        kind=kind,
        status_text=str(raw.get("status_text") or ""),
        last_seen_at=last_seen,
        started_at=started,
        resolved_at=resolved,
        notified=raw.get("notified") is True,
    )
