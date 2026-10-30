"""
Fronta věcí, které čekají na člověka.

Dva druhy položek:
- approval – agent chce provést rizikovou akci (retry_sync, create_ticket)
             a policy v kódu ji zastavila, dokud ji člověk neschválí / nezamítne;
- alert    – důležitá situace, kterou má člověk vidět (agent eskaloval,
             běh selhal, systém je nedostupný). Stačí ji vzít na vědomí.

Počet čekajících položek se zobrazuje jako červený odznak na záložce
„Administrace obchodů“. Na rozhodnutí naváže běh agenta (pokračuje
s výsledkem schválení nebo zamítnutí).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone

from core import storage, trace

COLLECTION = "approvals"

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
ACKNOWLEDGED = "acknowledged"


def _now() -> str:
    return timezone.localtime().isoformat(timespec="seconds")


def _create(kind: str, **fields: Any) -> dict[str, Any]:
    item = {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "status": PENDING,
        "created_at": _now(),
        "decided_at": None,
        "decided_by": None,
        "note": None,
        **fields,
    }
    return storage.insert(COLLECTION, item)


def request_approval(
    *, tool: str, args: dict[str, Any], reason: str, shop_id: str | None,
    order_id: str | None = None, run_id: str | None = None,
) -> dict[str, Any]:
    item = _create("approval", tool=tool, args=args, reason=reason,
                   shop_id=shop_id, order_id=order_id, run_id=run_id,
                   title=f"Agent žádá o schválení: {tool}")
    trace.record(trace.Actor.SCRIPT, "approval_requested",
                 f"Riziková akce {tool} pozastavena – čeká na schválení člověkem",
                 run_id=run_id, order_id=order_id, data={"approval_id": item["id"], "args": args})
    return item


def raise_alert(
    *, title: str, reason: str, shop_id: str | None,
    order_id: str | None = None, run_id: str | None = None, severity: str = "high",
) -> dict[str, Any]:
    item = _create("alert", title=title, reason=reason, severity=severity,
                   shop_id=shop_id, order_id=order_id, run_id=run_id)
    trace.record(trace.Actor.SCRIPT, "alert_raised", title,
                 run_id=run_id, order_id=order_id, data={"approval_id": item["id"]})
    return item


def pending(shop_id: str | None = None) -> list[dict[str, Any]]:
    items = storage.list_all(
        COLLECTION, lambda a: a["status"] == PENDING and (shop_id is None or a["shop_id"] == shop_id))
    return sorted(items, key=lambda a: a["created_at"], reverse=True)


def pending_counts() -> dict[str, Any]:
    by_shop: dict[str, int] = {}
    for item in pending():
        by_shop[item["shop_id"] or "_"] = by_shop.get(item["shop_id"] or "_", 0) + 1
    return {"total": sum(by_shop.values()), "by_shop": by_shop}


def decide(item_id: str, decision: str, *, by: str = "admin", note: str | None = None) -> dict[str, Any]:
    """decision: approve | deny | ack. Rozhodnout lze jen jednou."""
    item = storage.get(COLLECTION, "id", item_id)
    if item["status"] != PENDING:
        raise ValueError("O této položce už bylo rozhodnuto")

    allowed = {"approval": {"approve": APPROVED, "deny": DENIED}, "alert": {"ack": ACKNOWLEDGED}}
    status = allowed[item["kind"]].get(decision)
    if status is None:
        raise ValueError(f"Neplatné rozhodnutí {decision!r} pro {item['kind']}")

    item = storage.update(COLLECTION, "id", item_id,
                          {"status": status, "decided_at": _now(), "decided_by": by, "note": note})
    messages = {
        APPROVED: f"Člověk schválil akci {item.get('tool')}",
        DENIED: f"Člověk zamítl akci {item.get('tool')}",
        ACKNOWLEDGED: f"Člověk vzal na vědomí: {item['title']}",
    }
    trace.record(trace.Actor.HUMAN, f"approval_{status}", messages[status] + (f" – {note}" if note else ""),
                 run_id=item.get("run_id"), order_id=item.get("order_id"), data={"approval_id": item_id})
    return item
