from datetime import datetime

from django import template
from django.utils import timezone

register = template.Library()

# Lidsky čitelné české popisky stavů z JSON dat.
LABELS = {
    "new": "Nová", "paid": "Zaplaceno", "processing": "Zpracovává se", "shipped": "Odesláno",
    "cancelled": "Zrušeno", "pending": "Čeká na platbu", "failed": "Platba selhala",
    "not_received": "Sklad nepřijal", "received": "Přijato skladem", "on_hold": "Pozastaveno", "picking": "Vychystává se",
    "packed": "Zabaleno", "backorder": "Chybí zboží", "success": "OK", "error": "Chyba",
    "card": "Karta", "bank_transfer": "Převod", "cod": "Dobírka",
}

# Barevný „tón“ štítku: ok / warn / bad / neutral.
TONES = {
    "paid": "ok", "shipped": "ok", "success": "ok", "packed": "ok", "picking": "neutral",
    "processing": "neutral", "new": "neutral", "received": "ok", "pending": "warn", "on_hold": "warn",
    "backorder": "warn", "not_received": "bad", "error": "bad", "failed": "bad", "cancelled": "bad",
}


@register.filter
def label(value):
    return LABELS.get(value, value)


@register.filter
def tone(value):
    return TONES.get(value, "neutral")


@register.filter
def dt(value):
    if not value:
        return "–"
    try:
        return timezone.localtime(datetime.fromisoformat(value)).strftime("%d.%m. %H:%M:%S")
    except ValueError:
        return value


@register.filter
def money(value, currency="CZK"):
    if currency == "EUR":
        return f"{value:,.2f} €".replace(",", " ").replace(".", ",")
    return f"{value:,.0f} Kč".replace(",", " ")


@register.filter
def get_item(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def pretty_args(args):
    return ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
