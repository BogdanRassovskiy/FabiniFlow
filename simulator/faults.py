"""
Konfigurovatelná simulace poruch externích systémů.

Každý simulovaný systém má v `sim_config.json` záznam:
    mode        – ok | timeout | error | unavailable | flaky
    latency_ms  – umělé zpoždění každé odpovědi
    order_ids   – omezí poruchu jen na vybrané objednávky (prázdné = všechny)
    calls       – interní čítač volání (pro režim flaky)

Nastavuje se v administraci (záložka Simulace), takže lze během ukázky
„shodit“ např. WMS a sledovat, jak se s tím agent vypořádá.
"""

from __future__ import annotations

import time
from functools import wraps

from django.conf import settings
from django.http import JsonResponse

from core import storage

COLLECTION = "sim_config"

SYSTEMS = {
    "eshop": "E-shop (objednávky)",
    "payments": "Platební brána",
    "wms": "Sklad / WMS",
    "integration": "Integrace e-shop → WMS",
    "helpdesk": "Helpdesk (incidenty)",
}

MODES = {
    "ok": "V pořádku",
    "timeout": "Timeout – neodpovídá",
    "error": "HTTP 500 – interní chyba",
    "unavailable": "HTTP 503 – nedostupné",
    "flaky": "Nestabilní – každé 2. volání selže",
}


def get_config(system: str) -> dict:
    return storage.get(COLLECTION, "system", system)


def all_configs() -> list[dict]:
    return storage.list_all(COLLECTION)


def update_config(system: str, *, mode: str, latency_ms: int, order_ids: list[str]) -> dict:
    if mode not in MODES:
        raise ValueError(f"Neznámý režim: {mode}")
    return storage.update(COLLECTION, "system", system, {
        "mode": mode,
        "latency_ms": max(0, min(latency_ms, 10_000)),
        "order_ids": order_ids,
        "calls": 0,
    })


def error_response(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _next_call_number(system: str) -> int:
    with storage.transaction(COLLECTION) as records:
        record = next(r for r in records if r["system"] == system)
        record["calls"] = record.get("calls", 0) + 1
        return record["calls"]


def simulated(system: str):
    """Dekorátor pro view simulovaného systému: přidá zpoždění a případnou poruchu."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            config = get_config(system)
            time.sleep(config["latency_ms"] / 1000)

            order_id = kwargs.get("order_id")
            targeted = not config["order_ids"] or (order_id and order_id in config["order_ids"])
            mode = config["mode"] if targeted else "ok"

            if mode == "timeout":
                # Server „visí“ déle, než klient (agent) ochotně čeká.
                time.sleep(settings.MOCK_TIMEOUT_SECONDS)
                return error_response(504, "TIMEOUT", f"{SYSTEMS[system]} neodpověděl včas")
            if mode == "error":
                return error_response(500, "INTERNAL_ERROR", f"{SYSTEMS[system]}: neočekávaná chyba serveru")
            if mode == "unavailable":
                return error_response(503, "UNAVAILABLE", f"{SYSTEMS[system]} je dočasně nedostupný (údržba)")
            if mode == "flaky" and _next_call_number(system) % 2 == 1:
                return error_response(503, "UNAVAILABLE", f"{SYSTEMS[system]}: přechodná chyba, zkuste znovu")

            response = view(request, *args, **kwargs)
            response["X-Mock-System"] = system
            return response

        return wrapper

    return decorator
