"""
Registr nástrojů agenta.

Každý nástroj má JSON schéma pro tool calling a metadata, podle kterých
rozhoduje kód (ne model):
    risk         – read | risky | internal; risky vyžaduje schválení člověkem
    channels     – ve kterém kanálu je nástroj dostupný (ops = administrace, customer = chat)
    max_calls    – kolikrát smí být zavolán v jednom běhu
    dedupe       – stejné volání se stejnými argumenty se neprovede znovu (vrátí se výsledek z cache)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent import api_client, conversations, handoff, request_types
from core import trace

READ, RISKY, INTERNAL = "read", "risky", "internal"
OPS, CUSTOMER = "ops", "customer"


@dataclass
class RunContext:
    run_id: str
    channel: str
    shop_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[RunContext, dict[str, Any]], dict[str, Any]]
    risk: str = READ
    channels: frozenset[str] = frozenset({OPS, CUSTOMER})
    max_calls: int = 5
    dedupe: bool = False

    def schema(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _params(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


ORDER_ID = {"order_id": {"type": "string", "description": "Číslo objednávky, např. 100245"}}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


# --- Externí systémy -----------------------------------------------------------

def _customer_scope(ctx: RunContext, order_id: str) -> dict[str, Any] | None:
    """V zákaznickém chatu smí agent číst jen ověřenou objednávku daného zákazníka."""
    if ctx.channel != CUSTOMER:
        return None
    conv = conversations.get(ctx.conversation_id)
    fields = conv["draft"]["fields"]
    verified_order = fields.get("order_id", {})
    if not (verified_order.get("verified") and fields.get("email", {}).get("verified")):
        trace.record(trace.Actor.SCRIPT, "access_denied",
                     f"Přístup k objednávce {order_id} zablokován – zákazník ještě není ověřen",
                     run_id=ctx.run_id, order_id=order_id)
        return _error("CUSTOMER_NOT_VERIFIED",
                      "Nejdřív ověř zákazníka: číslo objednávky a e-mail musí být v požadavku ověřené.")
    if verified_order["value"] != order_id:
        trace.record(trace.Actor.SCRIPT, "access_denied",
                     f"Přístup k objednávce {order_id} zablokován – zákazník je ověřen jen pro {verified_order['value']}",
                     run_id=ctx.run_id, order_id=order_id)
        return _error("FORBIDDEN", f"Zákazník je ověřen jen pro objednávku {verified_order['value']}.")
    return None


REASON = {"reason": {"type": "string", "description": "Jednou větou proč akci navrhuješ – uvidí ji schvalovatel"}}


def _api(method: str, path: str, order_arg: bool = True):
    def handler(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
        args = {k: v for k, v in args.items() if k != "reason"}
        order_id = args.get("order_id")
        if order_arg:
            denied = _customer_scope(ctx, order_id)
            if denied:
                return denied
        return api_client.call(method, path.format(**args), run_id=ctx.run_id, order_id=order_id)
    return handler


def _create_ticket(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.channel == CUSTOMER and args.get("order_id"):
        denied = _customer_scope(ctx, args["order_id"])
        if denied:
            return denied
    body = {**{k: v for k, v in args.items() if k != "reason"}, "reporter": f"agent ({ctx.channel})"}
    return api_client.call("POST", "/helpdesk/tickets", json=body, run_id=ctx.run_id, order_id=args.get("order_id"))


# --- Zákaznický požadavek (chat) ----------------------------------------------

def _draft_tool(fn: Callable[..., Any]):
    def handler(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": fn(ctx, **args)}
        except conversations.DraftError as exc:
            return _error("REJECTED", str(exc))
        except (ValueError, TypeError) as exc:
            return _error("INVALID", str(exc))
    return handler


def _set_type(ctx, type):
    return conversations.set_request_type(ctx.conversation_id, type, run_id=ctx.run_id)


def _update_field(ctx, field, value):
    return conversations.update_request_field(ctx.conversation_id, field, value, run_id=ctx.run_id)


def _submit_request(ctx, summary, priority="normal"):
    return conversations.submit_request(ctx.conversation_id, summary=summary, priority=priority, run_id=ctx.run_id)


def _handoff(ctx, reason):
    item = handoff.request_handoff(ctx.conversation_id, reason=reason, run_id=ctx.run_id)
    return {"handoff_requested": True, "approval_id": item["id"],
            "instruction": "Žádost odeslána. Zákazníkovi nic nepiš a ukonči běh bez odpovědi."}


# --- Výsledek běhu (administrace) ---------------------------------------------

RESULT_STATUSES = ["resolved", "no_action_needed", "escalated", "needs_customer_action", "failed"]


def _submit_result(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": {"accepted": True}}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_order", "Načte objednávku z e-shopu: položky, zákazník, stav.",
         _params(ORDER_ID, ["order_id"]), _api("GET", "/eshop/orders/{order_id}"), dedupe=True),
    Tool("get_payment", "Stav platby objednávky v platební bráně.",
         _params(ORDER_ID, ["order_id"]), _api("GET", "/payments/{order_id}"), dedupe=True),
    Tool("get_fulfillment", "Stav expedice ve skladu (WMS): přijato, vychystává se, odesláno, chybí zboží…",
         _params(ORDER_ID, ["order_id"]), _api("GET", "/wms/fulfillments/{order_id}"), dedupe=True),
    Tool("get_sync_logs", "Historie předávání objednávky z e-shopu do skladu (WMS), včetně chyb.",
         _params(ORDER_ID, ["order_id"]), _api("GET", "/integration/sync-logs/{order_id}"), dedupe=True),
    Tool("retry_sync", "Znovu odešle objednávku do skladu. Riziková akce – provede se až po schválení člověkem. "
         "Použij jen při přechodné chybě synchronizace (TIMEOUT, 5xx), ne při chybě dat.",
         _params({**ORDER_ID, **REASON}, ["order_id", "reason"]), _api("POST", "/integration/retry-sync/{order_id}"),
         risk=RISKY, max_calls=1),
    Tool("create_ticket", "Založí incident pro IT/provoz s diagnostikou. Riziková akce – vyžaduje schválení.",
         _params({**ORDER_ID,
                  "description": {"type": "string", "description": "Co je špatně, co jsi zjistil a co doporučuješ"},
                  "priority": {"type": "string", "enum": ["low", "normal", "high"]}, **REASON},
                 ["description", "reason"]),
         _create_ticket, risk=RISKY, max_calls=1),

    Tool("set_request_type", "Určí typ zákaznického požadavku podle toho, co zákazník chce.",
         _params({"type": {"type": "string", "enum": list(request_types.REQUEST_TYPES),
                           "description": "; ".join(f"{k} = {t.label}" for k, t in request_types.REQUEST_TYPES.items())}},
                 ["type"]),
         _draft_tool(_set_type), risk=INTERNAL, channels=frozenset({CUSTOMER}), max_calls=3),
    Tool("update_request_field", "Zapíše do požadavku údaj, který zákazník sdělil. Výsledek ukáže, co ještě chybí.",
         _params({"field": {"type": "string", "description": "Klíč pole podle šablony typu (order_id, email, product…)"},
                  "value": {"type": "string"}}, ["field", "value"]),
         _draft_tool(_update_field), risk=INTERNAL, channels=frozenset({CUSTOMER}), max_calls=12),
    Tool("submit_request", "Založí kompletní požadavek. Projde jen, když jsou všechna povinná pole vyplněná a ověřená.",
         _params({"summary": {"type": "string", "description": "Interní shrnutí pro tým: co zákazník chce a co jsi zjistil"},
                  "priority": {"type": "string", "enum": ["low", "normal", "high"]}}, ["summary"]),
         _draft_tool(_submit_request), risk=INTERNAL, channels=frozenset({CUSTOMER}), max_calls=2),
    Tool("request_human_handoff", "Požádá administraci o převzetí chatu člověkem (podrážděný zákazník nebo žádost o člověka). "
         "Zákazník se o žádosti nedozví – po zavolání už nic nepiš.",
         _params({"reason": {"type": "string", "description": "Proč je potřeba člověk"}}, ["reason"]),
         _draft_tool(_handoff), risk=INTERNAL, channels=frozenset({CUSTOMER}), max_calls=1),

    Tool("submit_result", "Ukončí běh a odevzdá strukturovaný výsledek šetření. Volej vždy jako poslední krok. "
         "Provedené kroky doplní systém sám z historie volání.",
         _params({
             "status": {"type": "string", "enum": RESULT_STATUSES},
             "order_id": {"type": "string"},
             "summary": {"type": "string", "description": "Shrnutí jednou až dvěma větami"},
             "root_cause": {"type": "string", "description": "Zjištěná příčina (podložená výsledky nástrojů)"},
             "recommendation": {"type": "string", "description": "Doporučený další postup"},
         }, ["status", "summary", "root_cause", "recommendation"]),
         _submit_result, risk=INTERNAL, channels=frozenset({OPS}), max_calls=1),
]}


def for_channel(channel: str) -> list[Tool]:
    return [t for t in TOOLS.values() if channel in t.channels]
