"""
Předání zákaznického chatu člověku.

Kdy se o předání žádá:
- LLM  – agent pozná podráždění nebo žádost o člověka a zavolá nástroj
         request_human_handoff (s důvodem);
- SCRIPT – pojistka v kódu: výslovná žádost typu „chci mluvit s člověkem /
         operátorem“ spustí žádost i bez rozhodnutí modelu.

Zákazník o žádosti NEVÍ: nedostane žádnou zprávu ani změnu stavu a jeho
zpráva zůstane bez odpovědi, dokud nerozhodne člověk. Jinak by se snadno
naučil, jakou větou agenta obejít.

Žádost se objeví v administraci (fronta „Vyžaduje pozornost“):
- Převzít  → konverzace přejde do režimu `human`, agent mlčí a administrace
             píše zákazníkovi přímo; může ji pak vrátit agentovi;
- Odmítnout → konverzace zůstává agentovi, který ví, že předání neproběhlo,
             a pokračuje v rozhovoru sám.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from agent import approvals, conversations
from core import trace

AGENT = "agent"
PENDING = "handoff_pending"
HUMAN = "human"

# Výslovná žádost o člověka (čeština i slovenština, bez diakritiky).
_EXPLICIT = re.compile(
    r"(chci|chtel\w*|chcem|spojte|prepojte|dejte|dajte|potrebuj\w*|zavolejte|zavolajte|chcete)"
    r".{0,40}"
    r"(clovek\w*|clovece|operator\w*|ziv\w* osob\w*|nekym|niekym|koleg\w*|manazer\w*|vedouc\w*|nadriaden\w*)"
)

_TEXTS = {
    "cs": {
        "joined": "Připojil se kolega ze zákaznické podpory FABINI.",
        "returned": "Konverzaci opět přebírá virtuální asistent.",
    },
    "sk": {
        "joined": "Pripojil sa kolega zo zákazníckej podpory FABINI.",
        "returned": "Konverzáciu opäť preberá virtuálny asistent.",
    },
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _text(conv: dict[str, Any], key: str) -> str:
    from core import storage

    lang = storage.get("shops", "shop_id", conv["shop_id"]).get("language", "cs")
    return _TEXTS.get(lang, _TEXTS["cs"])[key]


def mode(conv: dict[str, Any]) -> str:
    return conv.get("mode") or AGENT


def customer_mode(conv: dict[str, Any]) -> str:
    """Režim, jak ho smí vidět zákazník – čekající žádost o předání se neprozrazuje."""
    return HUMAN if mode(conv) == HUMAN else AGENT


def check_explicit_request(conv: dict[str, Any], text: str) -> None:
    """Pojistka v kódu: výslovná žádost o člověka spustí předání i bez LLM."""
    if mode(conv) == AGENT and _EXPLICIT.search(_normalize(text)):
        request_handoff(conv["conversation_id"], reason=f"Zákazník výslovně žádá o člověka: „{text}“",
                        requested_by=trace.Actor.SCRIPT)


def request_handoff(conversation_id: str, *, reason: str, requested_by: trace.Actor = trace.Actor.LLM,
                    run_id: str | None = None) -> dict[str, Any]:
    conv = conversations.get(conversation_id)
    if mode(conv) != AGENT:
        raise conversations.DraftError("O předání člověku už bylo požádáno nebo chat vede člověk.")
    if (conv.get("handoff") or {}).get("status") == "declined" and requested_by == trace.Actor.LLM:
        # Po zamítnutí nežádáme hned znovu – rozhodne až další výslovná žádost zákazníka.
        raise conversations.DraftError("Administrace předání před chvílí zamítla – pokračuj v rozhovoru sám.")

    recent = [m["text"] for m in conv["messages"] if m["role"] == "customer"][-3:]
    item = approvals.request_handoff(
        conversation_id=conversation_id, reason=reason, recent_messages=recent,
        shop_id=conv["shop_id"], order_id=conversations.order_id_of(conv), run_id=run_id,
        requested_by=str(requested_by))
    conv["mode"] = PENDING
    conv["handoff"] = {"approval_id": item["id"], "status": "pending", "reason": reason,
                       "requested_by": str(requested_by), "note": None}
    conversations.save(conv)
    trace.record(requested_by, "handoff_requested", f"Žádost o předání chatu člověku – {reason}",
                 run_id=run_id, order_id=conversations.order_id_of(conv),
                 data={"conversation_id": conversation_id})
    return item


def apply_decision(conversation_id: str, accepted: bool, note: str | None) -> None:
    """Volá se po rozhodnutí administrace (agent.approvals.decide)."""
    conv = conversations.get(conversation_id)
    conv["handoff"].update(status="accepted" if accepted else "declined", note=note)
    conv["mode"] = HUMAN if accepted else AGENT
    conversations.save(conv)
    if accepted:
        conversations.add_message(conversation_id, "system", _text(conv, "joined"))
    else:
        # Zákazník dosud čeká bez odpovědi – agent odpoví sám (ví, že předání neproběhlo).
        from agent import runner  # runner importuje tento modul

        runner.start_customer_run(conversation_id)


def operator_message(conversation_id: str, text: str, *, operator: str = "admin") -> dict[str, Any]:
    conv = conversations.get(conversation_id)
    if mode(conv) != HUMAN:
        raise conversations.DraftError("Chat právě nevede člověk.")
    text = text.strip()
    if not text:
        raise conversations.DraftError("Zpráva je prázdná.")
    trace.record(trace.Actor.HUMAN, "operator_message", f"Operátor odpověděl zákazníkovi: {text[:120]}",
                 order_id=conversations.order_id_of(conv), data={"conversation_id": conversation_id})
    return conversations.add_message(conversation_id, "operator", text)


def return_to_agent(conversation_id: str) -> None:
    conv = conversations.get(conversation_id)
    if mode(conv) != HUMAN:
        raise conversations.DraftError("Chat právě nevede člověk.")
    conv["mode"] = AGENT
    conv["handoff"]["status"] = "returned"
    conversations.save(conv)
    trace.record(trace.Actor.HUMAN, "handoff_returned", "Operátor vrátil konverzaci agentovi",
                 order_id=conversations.order_id_of(conv), data={"conversation_id": conversation_id})
    conversations.add_message(conversation_id, "system", _text(conv, "returned"))
