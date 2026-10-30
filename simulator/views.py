"""
Simulované externí systémy FABINI – každý endpoint se chová jako samostatné API.

Formát odpovědí:
    úspěch  {"data": ...}                              HTTP 200 / 201
    chyba   {"error": {"code": ..., "message": ...}}   HTTP 4xx / 5xx

Poruchy (timeout, 5xx, nestabilita, zpoždění) přidává dekorátor `simulated`
podle nastavení v administraci. Business logika (retry, incidenty) je níže.
"""

from __future__ import annotations

import json
import random
from datetime import datetime

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core import storage
from simulator.faults import error_response, simulated


def _now() -> str:
    return timezone.localtime().isoformat(timespec="seconds")


def _order_or_404(order_id: str):
    order = storage.find("orders", "order_id", order_id)
    if order is None:
        return None, error_response(404, "ORDER_NOT_FOUND", f"Objednávka {order_id} neexistuje")
    return order, None


# --- E-shop -----------------------------------------------------------------

@require_GET
@simulated("eshop")
def get_order(request, order_id):
    order, err = _order_or_404(order_id)
    return err or JsonResponse({"data": order})


# --- Platební brána ---------------------------------------------------------

@require_GET
@simulated("payments")
def get_payment(request, order_id):
    payment = storage.find("payments", "order_id", order_id)
    if payment is None:
        return error_response(404, "PAYMENT_NOT_FOUND", f"K objednávce {order_id} neexistuje platba")
    return JsonResponse({"data": payment})


# --- Sklad / WMS ------------------------------------------------------------

@require_GET
@simulated("wms")
def get_fulfillment(request, order_id):
    fulfillment = storage.find("fulfillments", "order_id", order_id)
    if fulfillment is None:
        return error_response(404, "FULFILLMENT_NOT_FOUND", f"WMS objednávku {order_id} nezná")
    return JsonResponse({"data": fulfillment})


# --- Integrace e-shop → WMS -------------------------------------------------

@require_GET
@simulated("integration")
def get_sync_logs(request, order_id):
    _, err = _order_or_404(order_id)
    if err:
        return err
    logs = storage.list_all("sync_logs", lambda log: log["order_id"] == order_id)
    return JsonResponse({"data": logs})


@csrf_exempt
@require_POST
@simulated("integration")
def retry_sync(request, order_id):
    order, err = _order_or_404(order_id)
    if err:
        return err

    payment = storage.find("payments", "order_id", order_id) or {}
    if payment.get("status") != "paid" and payment.get("method") != "cod":
        return error_response(409, "NOT_PAID", "Nezaplacenou objednávku nelze předat do skladu")

    logs = storage.list_all("sync_logs", lambda log: log["order_id"] == order_id)
    if logs and logs[-1]["status"] == "success":
        fulfillment = storage.find("fulfillments", "order_id", order_id) or {}
        return error_response(409, "ALREADY_SYNCED",
                              f"Objednávka už je ve WMS ({fulfillment.get('wms_order_id')}), opakování není potřeba")

    shop = storage.get("shops", "shop_id", order["shop_id"])
    warehouse = storage.get("warehouses", "warehouse_id", shop["warehouse_id"])
    rule = storage.find("simulation", "order_id", order_id) or {}
    attempt = len(logs) + 1
    log = {
        "id": f"s-{datetime.now().strftime('%H%M%S%f')}",
        "order_id": order_id,
        "ts": _now(),
        "source": shop["domain"],
        "target": warehouse["system"],
        "attempt": attempt,
        "trigger": "manual_retry",
    }

    if rule.get("retry_sync", "success") == "success":
        wms_order_id = f"{warehouse['warehouse_id'].replace('WH', 'WMS')}-{random.randint(56000, 56999)}"
        storage.insert("sync_logs", {**log, "status": "success",
                                     "message": f"Objednávka předána do WMS ({wms_order_id})"})
        storage.update("fulfillments", "order_id", order_id,
                       {"status": "received", "wms_order_id": wms_order_id, "updated_at": _now()})
        storage.update("orders", "order_id", order_id, {"status": "processing"})
        return JsonResponse({"data": {"result": "success", "attempt": attempt, "wms_order_id": wms_order_id}})

    storage.insert("sync_logs", {**log, "status": "error", "error_code": rule.get("error_code", "UNKNOWN"),
                                 "message": rule.get("error", "Synchronizace selhala")})
    # Volání API proběhlo, ale synchronizace sama selhala – proto 200 s výsledkem "error".
    return JsonResponse({"data": {"result": "error", "attempt": attempt,
                                  "error_code": rule.get("error_code", "UNKNOWN"),
                                  "message": rule.get("error", "Synchronizace selhala")}})


# --- Helpdesk ---------------------------------------------------------------

@csrf_exempt
@require_POST
@simulated("helpdesk")
def create_ticket(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response(400, "BAD_REQUEST", "Tělo požadavku není platný JSON")

    description = (body.get("description") or "").strip()
    if len(description) < 10:
        return error_response(422, "VALIDATION", "Popis incidentu musí mít alespoň 10 znaků")

    order_id = body.get("order_id")
    shop_id = None
    if order_id:
        order, err = _order_or_404(order_id)
        if err:
            return err
        shop_id = order["shop_id"]
        # Idempotence: k jedné objednávce nevznikne víc otevřených incidentů.
        existing = storage.list_all(
            "tickets", lambda t: t.get("order_id") == order_id and t["status"] == "open")
        if existing:
            return JsonResponse({"data": {**existing[0], "duplicate": True}})

    ticket = {
        "ticket_id": f"INC-{1001 + len(storage.list_all('tickets'))}",
        "order_id": order_id,
        "shop_id": shop_id,
        "priority": body.get("priority") if body.get("priority") in ("low", "normal", "high") else "normal",
        "description": description,
        "reporter": body.get("reporter", "unknown"),
        "status": "open",
        "created_at": _now(),
    }
    storage.insert("tickets", ticket)
    return JsonResponse({"data": ticket}, status=201)
