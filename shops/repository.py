"""
Čtecí vrstva nad doménovými JSON kolekcemi virtuálních e-shopů.

Slouží UI (přehledy). Nástroje agenta (get_order, get_payment, ...) budou mít
vlastní modul s chováním „externího API“ včetně simulovaných chyb.
"""

from __future__ import annotations

from typing import Any

from core import storage


def shops() -> list[dict[str, Any]]:
    return storage.list_all("shops")


def shop(shop_id: str) -> dict[str, Any]:
    return storage.get("shops", "shop_id", shop_id)


def products(shop_id: str) -> list[dict[str, Any]]:
    return storage.list_all("products", lambda p: p["shop_id"] == shop_id)


def warehouse(warehouse_id: str) -> dict[str, Any]:
    return storage.get("warehouses", "warehouse_id", warehouse_id)


def order_board(shop_id: str) -> list[dict[str, Any]]:
    """Objednávky obchodu spojené se stavem platby, expedice a poslední synchronizace."""
    payments = {p["order_id"]: p for p in storage.list_all("payments")}
    fulfillments = {f["order_id"]: f for f in storage.list_all("fulfillments")}
    last_sync: dict[str, dict[str, Any]] = {}
    for log in storage.list_all("sync_logs"):
        last_sync[log["order_id"]] = log  # logy jsou chronologicky, vyhraje poslední

    rows = []
    for order in storage.list_all("orders", lambda o: o["shop_id"] == shop_id):
        oid = order["order_id"]
        rows.append({
            **order,
            "payment": payments.get(oid),
            "fulfillment": fulfillments.get(oid),
            "last_sync": last_sync.get(oid),
        })
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)
