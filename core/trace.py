"""
Trace log – chronologický záznam všeho, co se v systému stalo, a KDO o tom rozhodl.

Rozlišujeme čtyři typy aktérů, aby bylo při kontrole vidět, co bylo
rozhodnutí jazykového modelu a co deterministická logika v kódu:

- LLM    – rozhodnutí modelu (volba nástroje, závěr, doporučení)
- SCRIPT – deterministické pravidlo v kódu (policy, guardrail, limit, validace)
- HUMAN  – akce člověka (schválení/zamítnutí, zadání požadavku, nová objednávka)
- SYSTEM – událost prostředí (výsledek/chyba volání API, timeout)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from core import storage

COLLECTION = "trace"


class Actor(StrEnum):
    LLM = "LLM"
    SCRIPT = "SCRIPT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


def record(
    actor: Actor,
    event: str,
    message: str,
    *,
    run_id: str | None = None,
    order_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "actor": str(actor),
        "event": event,
        "message": message,
        "run_id": run_id,
        "order_id": order_id,
        "data": data or {},
    }
    return storage.insert(COLLECTION, entry)


def entries(*, run_id: str | None = None, order_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    def match(e: dict[str, Any]) -> bool:
        return (run_id is None or e["run_id"] == run_id) and (order_id is None or e["order_id"] == order_id)

    return list(reversed(storage.list_all(COLLECTION, match)))[:limit]
