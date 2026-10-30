"""
Šablony zákaznických požadavků.

Cílem agenta v zákaznickém chatu je během rozhovoru vyplnit kompletní
požadavek: nejdřív určí typ, pak postupně doptává chybějící pole. Šablona
říká, která pole typ potřebuje, která jsou povinná a jak se ověřují.

Ověření (`verify`) dělá kód, ne model:
- order_in_shop  – objednávka existuje a patří obchodu, ve kterém zákazník píše
- order_email    – e-mail se shoduje s e-mailem u objednávky (ověření vlastníka)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str  # co se má agent zeptat / co sem patří
    required: bool = True
    verify: str | None = None
    choices: tuple[str, ...] = ()


ORDER_ID = Field("order_id", "Číslo objednávky", "šestimístné číslo z potvrzovacího e-mailu",
                 verify="order_in_shop")
EMAIL = Field("email", "E-mail zákazníka", "e-mail použitý při objednávce (ověření vlastníka)",
              verify="order_email")
PRODUCT = Field("product", "Produkt", "kterého produktu z objednávky se požadavek týká")
DESCRIPTION = Field("description", "Popis problému", "stručně vlastními slovy zákazníka")
ATTACHMENT = Field("attachment", "Příloha", "snímek obrazovky nebo fotka, pokud ji zákazník má",
                   required=False)


@dataclass(frozen=True)
class RequestType:
    key: str
    label: str
    description: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


REQUEST_TYPES: dict[str, RequestType] = {t.key: t for t in [
    RequestType(
        "order_status", "Stav objednávky",
        "Objednávka nedorazila, nebyla odeslána, zákazník neví, kde je.",
        (ORDER_ID, EMAIL, DESCRIPTION, ATTACHMENT),
    ),
    RequestType(
        "payment", "Problém s platbou",
        "Platba proběhla, ale objednávka se tváří nezaplaceně; dvojí platba; chyba při placení.",
        (ORDER_ID, EMAIL, DESCRIPTION,
         Field("attachment", "Doklad o platbě", "snímek z bankovnictví nebo potvrzení platby", required=False)),
    ),
    RequestType(
        "complaint", "Reklamace",
        "Vadné nebo poškozené zboží (FABINI Garance).",
        (ORDER_ID, EMAIL, PRODUCT,
         Field("description", "Popis vady", "co přesně je s výrobkem špatně a od kdy"),
         Field("resolution", "Požadované řešení", "co zákazník preferuje",
               choices=("výměna", "oprava", "vrácení peněz")),
         Field("attachment", "Fotka vady", "fotka poškození výrazně urychlí vyřízení", required=False)),
    ),
    RequestType(
        "return", "Vrácení zboží",
        "Zákazník chce zboží vrátit bez vady.",
        (ORDER_ID, EMAIL, PRODUCT,
         Field("description", "Důvod vrácení", "proč zákazník zboží vrací")),
    ),
    RequestType(
        "other", "Jiný dotaz",
        "Cokoli jiného (produkty, péče o nádobí, doprava…).",
        (EMAIL, DESCRIPTION),
    ),
]}


def get(type_key: str) -> RequestType:
    try:
        return REQUEST_TYPES[type_key]
    except KeyError:
        raise ValueError(f"Neznámý typ požadavku {type_key!r}. Povolené: {', '.join(REQUEST_TYPES)}")


def as_dict() -> dict:
    """Serializace pro UI a pro popis nástroje agentovi."""
    return {
        t.key: {
            "label": t.label,
            "description": t.description,
            "fields": [
                {"key": f.key, "label": f.label, "hint": f.hint, "required": f.required,
                 "verify": f.verify, "choices": list(f.choices)}
                for f in t.fields
            ],
        }
        for t in REQUEST_TYPES.values()
    }
