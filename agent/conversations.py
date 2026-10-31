"""
Zákaznické konverzace a rozpracovaný požadavek (draft), který agent během
rozhovoru vyplňuje.

Model (LLM) rozhoduje, JAKÝ je typ požadavku a CO zákazník řekl – volá
nástroje set_request_type / update_request_field / submit_request.
Kód (SCRIPT) rozhoduje, zda je hodnota platná a ověřená, a nepustí
odeslání neúplného nebo neověřeného požadavku.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone

from agent import approvals, request_types
from core import storage, trace

COLLECTION = "conversations"
REQUESTS = "requests"
MAX_EMAIL_FAILURES = 3


class DraftError(ValueError):
    """Chyba, kterou agent dostane jako výsledek nástroje a má na ni reagovat."""


def _now() -> str:
    return timezone.localtime().isoformat(timespec="seconds")


def _empty_draft() -> dict[str, Any]:
    return {"type": None, "fields": {}, "email_failures": 0, "locked": False}


# --- Konverzace -------------------------------------------------------------

def start(shop_id: str) -> dict[str, Any]:
    storage.get("shops", "shop_id", shop_id)
    conv = {
        "conversation_id": f"c-{uuid.uuid4().hex[:8]}",
        "shop_id": shop_id,
        "created_at": _now(),
        "status": "open",
        "messages": [],
        "draft": _empty_draft(),
        "request_id": None,
        "mode": "agent",  # agent | handoff_pending | human (viz agent.handoff)
        "handoff": None,
    }
    return storage.insert(COLLECTION, conv)


def get(conversation_id: str) -> dict[str, Any]:
    return storage.get(COLLECTION, "conversation_id", conversation_id)


def save(conv: dict[str, Any]) -> dict[str, Any]:
    return storage.update(COLLECTION, "conversation_id", conv["conversation_id"], conv)


@storage.atomic
def add_message(conversation_id: str, role: str, text: str, attachment: str | None = None) -> dict[str, Any]:
    """role: customer | agent | operator | system (systémová poznámka viditelná oběma stranám)."""
    conv = get(conversation_id)
    conv["messages"].append({"role": role, "text": text, "attachment": attachment, "ts": _now()})
    conv = save(conv)
    if role == "customer":
        from agent import handoff  # handoff importuje tento modul

        handoff.check_explicit_request(conv, text)
    return get(conversation_id)


# --- Draft požadavku ---------------------------------------------------------

@storage.atomic
def set_request_type(conversation_id: str, type_key: str, *, run_id: str | None = None) -> dict[str, Any]:
    conv = get(conversation_id)
    _ensure_editable(conv)
    rtype = request_types.get(type_key)
    draft = conv["draft"]
    # Při změně typu ponecháme pole, která má i nový typ (číslo objednávky, e-mail…).
    keep = {f.key for f in rtype.fields}
    draft["fields"] = {k: v for k, v in draft["fields"].items() if k in keep}
    draft["type"] = type_key
    _verify_all(conv, run_id)
    save(conv)
    trace.record(trace.Actor.LLM, "request_type_set", f"Typ požadavku určen: {rtype.label}",
                 run_id=run_id, order_id=order_id_of(conv), data={"conversation_id": conversation_id})
    return draft_summary(conv)


@storage.atomic
def update_request_field(conversation_id: str, key: str, value: str, *,
                         source: str = "customer", run_id: str | None = None) -> dict[str, Any]:
    conv = get(conversation_id)
    _ensure_editable(conv)
    draft = conv["draft"]
    if not draft["type"]:
        spec = {f.key: f for f in request_types.IDENTITY_FIELDS}.get(key)
        if spec is None:
            raise DraftError("Před určením typu lze zapsat jen order_id a email. Ostatní údaje po set_request_type.")
    else:
        spec = {f.key: f for f in request_types.get(draft["type"]).fields}.get(key)
    if spec is None:
        raise DraftError(f"Typ {draft['type']} nemá pole {key!r}. Povolená pole: {', '.join(f.key for f in request_types.get(draft['type']).fields)}")

    value = str(value).strip()
    if spec.choices:
        value = value.lower()
        if value not in spec.choices:
            raise DraftError(f"Pole {key} musí být jedna z možností: {', '.join(spec.choices)}")
    if not value:
        raise DraftError("Hodnota nesmí být prázdná")

    draft["fields"][key] = {"value": value, "source": source, "verified": None, "error": None, "updated_at": _now()}
    trace.record(trace.Actor.LLM, "request_field_set", f"{spec.label}: {value}",
                 run_id=run_id, order_id=order_id_of(conv), data={"conversation_id": conversation_id, "field": key})
    _verify_all(conv, run_id)
    save(conv)
    return draft_summary(conv)


@storage.atomic
def submit_request(conversation_id: str, *, summary: str, priority: str = "normal",
                   run_id: str | None = None) -> dict[str, Any]:
    conv = get(conversation_id)
    _ensure_editable(conv)
    state = draft_summary(conv)
    if not state["type"] or state["missing"]:
        trace.record(trace.Actor.SCRIPT, "request_submit_blocked",
                     "Odeslání zablokováno – požadavek není kompletní nebo ověřený: "
                     + ", ".join(state["missing"] or ["typ"]),
                     run_id=run_id, order_id=order_id_of(conv))
        raise DraftError("Požadavek nelze odeslat, chybí nebo nejsou ověřena pole: "
                         + ", ".join(state["missing"] or ["type"]))

    request = {
        "request_id": f"REQ-{1001 + len(storage.list_all(REQUESTS))}",
        "conversation_id": conversation_id,
        "shop_id": conv["shop_id"],
        "type": conv["draft"]["type"],
        "type_label": request_types.get(conv["draft"]["type"]).label,
        "fields": {k: v["value"] for k, v in conv["draft"]["fields"].items()},
        "summary": summary.strip(),
        "priority": priority if priority in ("low", "normal", "high") else "normal",
        "status": "new",
        "created_at": _now(),
    }
    storage.insert(REQUESTS, request)
    conv["status"] = "submitted"
    conv["request_id"] = request["request_id"]
    save(conv)
    trace.record(trace.Actor.SCRIPT, "request_submitted",
                 f"Požadavek {request['request_id']} ({request['type_label']}) založen – všechna povinná pole ověřena",
                 run_id=run_id, order_id=request["fields"].get("order_id"),
                 data={"conversation_id": conversation_id, "request_id": request["request_id"]})
    return request


# --- Ověřování (deterministicky v kódu) ---------------------------------------

def _verify_all(conv: dict[str, Any], run_id: str | None) -> None:
    fields = conv["draft"]["fields"]
    order = None
    if "order_id" in fields:
        order = storage.find("orders", "order_id", fields["order_id"]["value"])
        ok = order is not None and order["shop_id"] == conv["shop_id"]
        _mark(conv, "order_id", ok, None if ok else "Objednávka v tomto obchodě neexistuje", run_id)
        if not ok:
            order = None

    if "email" in fields:
        if order is None:
            fields["email"].update(verified=None, error="Ověří se po zadání platného čísla objednávky")
        else:
            ok = fields["email"]["value"].lower() == order["customer"]["email"].lower()
            was = fields["email"]["verified"]
            _mark(conv, "email", ok, None if ok else "E-mail nesouhlasí s objednávkou", run_id)
            if not ok and was is not False:
                _email_failed(conv, run_id)

    for key, f in fields.items():
        if key not in ("order_id", "email") and f["verified"] is None:
            f["verified"] = True  # volný text – stačí, že ho zákazník uvedl


def _mark(conv, key, ok: bool, error: str | None, run_id) -> None:
    f = conv["draft"]["fields"][key]
    if f["verified"] == ok and f["error"] == error:
        return
    f.update(verified=ok, error=error)
    label = {"order_id": "Číslo objednávky", "email": "E-mail"}[key]
    trace.record(trace.Actor.SCRIPT, "field_verified" if ok else "field_verification_failed",
                 f"{label} {'ověřen' if ok else 'neověřen'}: {f['value']}" + ("" if ok else f" – {error}"),
                 run_id=run_id, order_id=order_id_of(conv))


def _email_failed(conv, run_id) -> None:
    draft = conv["draft"]
    draft["email_failures"] += 1
    if draft["email_failures"] >= MAX_EMAIL_FAILURES:
        draft["locked"] = True
        approvals.raise_alert(
            title="Opakovaně neúspěšné ověření zákazníka v chatu",
            reason=f"{MAX_EMAIL_FAILURES}× nesouhlasil e-mail s objednávkou {order_id_of(conv)}. "
                   "Konverzace je zamčená – může jít o pokus získat cizí údaje.",
            shop_id=conv["shop_id"], order_id=order_id_of(conv), run_id=run_id)


def _ensure_editable(conv) -> None:
    if conv["status"] != "open":
        raise DraftError(f"Požadavek už byl odeslán ({conv['request_id']}).")
    if conv["draft"]["locked"]:
        raise DraftError("Konverzace je zamčená po opakovaném neúspěšném ověření – předej ji člověku.")


def order_id_of(conv) -> str | None:
    f = conv["draft"]["fields"].get("order_id")
    return f["value"] if f else None


# --- Pohled na draft ----------------------------------------------------------

def draft_summary(conv: dict[str, Any]) -> dict[str, Any]:
    """Stav draftu pro UI i pro agenta (výsledek nástrojů): co je vyplněné a co chybí."""
    draft = conv["draft"]
    rtype = request_types.get(draft["type"]) if draft["type"] else None
    specs = rtype.fields if rtype else [f for f in request_types.IDENTITY_FIELDS if f.key in draft["fields"]]
    rows, missing = [], []
    for spec in specs:
        state = draft["fields"].get(spec.key)
        rows.append({
            "key": spec.key, "label": spec.label, "hint": spec.hint, "required": spec.required,
            "choices": list(spec.choices),
            "value": state["value"] if state else None,
            "source": state["source"] if state else None,
            "verified": state["verified"] if state else None,
            "error": state["error"] if state else None,
            "verifiable": spec.verify is not None,
        })
        if rtype and spec.required and not (state and state["verified"]):
            missing.append(spec.key)
    if not rtype:
        return {"type": None, "type_label": None, "fields": rows, "missing": ["type"], "filled": 0, "total": 0,
                "ready": False, "locked": draft["locked"], "status": conv["status"], "request_id": conv["request_id"]}

    required = [r for r in rows if r["required"]]
    return {
        "type": rtype.key, "type_label": rtype.label, "fields": rows, "missing": missing,
        "filled": sum(1 for r in required if r["key"] not in missing), "total": len(required),
        "ready": not missing, "locked": draft["locked"], "status": conv["status"], "request_id": conv["request_id"],
    }
