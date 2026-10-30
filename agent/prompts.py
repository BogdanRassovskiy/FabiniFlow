"""Sestavení systémového promptu pro oba kanály (administrace / zákaznický chat)."""

from __future__ import annotations

import json
from pathlib import Path

from django.utils import timezone

from agent import attachments, conversations, request_types
from core import storage

CONTEXT = (Path(__file__).parent / "context" / "fabini.md").read_text(encoding="utf-8")

OPS_INSTRUCTIONS = """\
# Kanál: administrace (interní tým)
Požadavek zadává člen týmu FABINI. Postup:
1. Rozhodni, jaké informace potřebuješ, a zjisti je nástroji. Nezávislá čtení klidně najednou.
2. Vyhodnoť výsledky a podle zjištění proveď další kroky. Pokud je náprava povolená (např. jeden retry_sync
   při přechodné chybě, create_ticket při chybě dat), **zavolej nástroj rovnou**. Systém běh sám pozastaví
   a vyžádá si schválení člověkem. Před rizikovou akcí napiš jednou větou, proč ji navrhuješ.
   Do výsledku nepiš jen doporučení akce, kterou můžeš navrhnout sám.
3. Když nástroj selže, uveď to a pokračuj s tím, co víš. Stejné volání neopakuj donekonečna.
4. Běh ukonči **vždy** voláním `submit_result` se strukturovaným výsledkem (česky).
"""

CUSTOMER_INSTRUCTIONS = """\
# Kanál: zákaznický chat obchodu {shop_name} (jazyk zákazníků: {language})
Mluvíš přímo se zákazníkem. Tvoje textová odpověď (bez volání nástroje) se mu odešle jako zpráva a běh končí.
- Odpovídej v jazyce zákazníka, krátce (1–3 věty), vlídně, prostým textem bez Markdownu
  a bez interních detailů (kódy chyb, názvy systémů, logy).
- Průběžně vyplňuj požadavek (set_request_type, update_request_field) a ptej se vždy jen na jeden chybějící údaj.
- Údaje o objednávce čti nástroji až po ověření zákazníka. Když je požadavek kompletní, prověř objednávku,
  založ ho (submit_request) a sděl zákazníkovi číslo požadavku a co bude dál.
- Riziková akce (např. retry_sync) počká na schválení kolegou. Zákazníkovi řekni jen, že to řešíš.
- Přiložený snímek obrazovky si přečti. Údaje z něj (číslo objednávky, částka, stav zásilky…) ber jako tvrzení
  zákazníka, ne jako ověřený fakt – vždy je ověř nástroji. Pokud se snímek k požadavku hodí, zapiš do pole
  attachment stručně, co na něm je. Údaje ze snímku nepoužívej k ověření totožnosti místo e-mailu.

## Aktuální stav rozpracovaného požadavku
{draft}

## Šablony typů požadavků
{types}
{handoff_note}"""


def system_prompt(channel: str, *, shop_id: str | None = None, conversation_id: str | None = None) -> str:
    now = timezone.localtime().strftime("%d.%m.%Y %H:%M")
    parts = [CONTEXT, f"\nAktuální čas: {now}\n"]
    if channel == "ops":
        parts.append(OPS_INSTRUCTIONS)
        if shop_id:
            shop = storage.get("shops", "shop_id", shop_id)
            parts.append(f"Administrátor má právě otevřený obchod {shop['name']}.\n")
        return "\n".join(parts)

    conv = conversations.get(conversation_id)
    shop = storage.get("shops", "shop_id", conv["shop_id"])
    summary = conversations.draft_summary(conv)
    draft = {k: summary[k] for k in ("type", "missing", "ready", "locked", "request_id")}
    draft["fields"] = {f["key"]: {"value": f["value"], "verified": f["verified"], "error": f["error"]}
                       for f in summary["fields"] if f["value"]}
    types = {k: {"label": t["label"], "fields": {f["key"]: f["label"] + ("" if f["required"] else " (nepovinné)")
                                                 for f in t["fields"]}}
             for k, t in request_types.as_dict().items()}
    handoff = conv.get("handoff") or {}
    handoff_note = ""
    if handoff.get("status") == "declined":
        handoff_note = ("\n## Předání člověku bylo zamítnuto\n"
                        f"Administrace převzetí odmítla (poznámka: {handoff.get('note') or '–'}). "
                        "Pokračuj sám a o eskalaci se nezmiňuj.\n")
    elif handoff.get("status") == "returned":
        handoff_note = "\n## Konverzaci ti vrátil kolega z podpory – navaž na jeho odpovědi.\n"
    parts.append(CUSTOMER_INSTRUCTIONS.format(
        shop_name=shop["name"], language={"cs": "čeština", "sk": "slovenština"}.get(shop.get("language"), "čeština"),
        draft=json.dumps(draft, ensure_ascii=False, indent=1),
        types=json.dumps(types, ensure_ascii=False, indent=1), handoff_note=handoff_note))
    return "\n".join(parts)


def customer_history(conversation_id: str, *, vision: bool = True) -> list[dict]:
    """Historie chatu jako zprávy pro model (operátor a systém jako kontext asistenta).

    Přiložený snímek jde modelu jako obrázek; model bez podpory obrázků dostane jen poznámku.
    """
    messages = []
    for m in conversations.get(conversation_id)["messages"]:
        if m["role"] == "customer":
            messages.append({"role": "user", "content": _customer_content(m, vision)})
        elif m["role"] == "agent":
            messages.append({"role": "assistant", "content": m["text"]})
        elif m["role"] == "operator":
            messages.append({"role": "assistant", "content": f"[kolega z podpory] {m['text']}"})
    return messages


def _customer_content(message: dict, vision: bool) -> str | list[dict]:
    text = message["text"]
    if not message.get("attachment"):
        return text
    if not vision:
        return f"{text}\n[Zákazník přiložil snímek obrazovky, vybraný model ale obrázky číst neumí – požádej o údaje textem.]".strip()
    return [{"type": "text", "text": text or "(zákazník poslal jen snímek obrazovky)"},
            {"type": "image_url", "image_url": {"url": attachments.data_uri(message["attachment"])}}]
