"""
Přílohy v zákaznickém chatu (simulace).

Zákazník nenahrává vlastní soubory – vybírá z připravených snímků obrazovky.
Snímek se modelu posílá jako obrázek (data URI), pokud vybraný model umí číst
obrázky. Jinak dostane jen textovou poznámku, že příloha existuje.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from django.conf import settings
from django.templatetags.static import static

DIR = Path(settings.BASE_DIR) / "web" / "static" / "web" / "screenshots"

SAMPLES = [
    {"file": "platba.png", "shop": "fabini_cz", "caption": "Potvrzení platby z bankovnictví (100245)"},
    {"file": "zasilka.png", "shop": "fabini_cz", "caption": "Sledování zásilky – „nedoručeno“ (100241)"},
    {"file": "email.png", "shop": "fabini_sk", "caption": "E-mail „Objednávka prijatá“ (100247)"},
]


def for_shop(shop_id: str) -> list[dict[str, Any]]:
    return [s for s in SAMPLES if s["shop"] == shop_id]


def get(file: str, shop_id: str) -> dict[str, Any] | None:
    """Jen snímek nabízený danému obchodu – jiný název souboru se odmítne."""
    return next((s for s in for_shop(shop_id) if s["file"] == file), None)


def url(file: str) -> str:
    return static(f"web/screenshots/{file}")


def data_uri(file: str) -> str:
    encoded = base64.b64encode((DIR / file).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
