"""
HTTP klient na (simulované) externí systémy FABINI.

Agent s nimi mluví stejně jako s reálnými API: přes HTTP, s timeoutem a
s opakováním přechodných chyb (timeout, 502/503/504) s backoffem.
Trvalé chyby (4xx, 500) se neopakují – vrací se agentovi jako výsledek.
Každá odpověď systému se zapíše do trace jako SYSTEM.
"""

from __future__ import annotations

import time
from typing import Any

import requests
from django.conf import settings

from core import trace

TIMEOUT = 5
MAX_RETRIES = 2
TRANSIENT = {502, 503, 504}


def call(method: str, path: str, *, json: dict | None = None,
         run_id: str | None = None, order_id: str | None = None) -> dict[str, Any]:
    url = f"{settings.MOCK_API_BASE_URL}{path}"
    for attempt in range(1, MAX_RETRIES + 2):
        started = time.monotonic()
        try:
            res = requests.request(method, url, json=json, timeout=TIMEOUT)
        except requests.Timeout:
            outcome = {"ok": False, "error": {"code": "TIMEOUT", "message": f"Systém neodpověděl do {TIMEOUT} s"}}
            transient = True
        except requests.RequestException as exc:
            outcome = {"ok": False, "error": {"code": "CONNECTION", "message": f"Systém je nedostupný ({exc.__class__.__name__})"}}
            transient = True
        else:
            body = _json(res)
            if res.ok:
                outcome = {"ok": True, "data": body.get("data")}
                transient = False
            else:
                outcome = {"ok": False, "http_status": res.status_code,
                           "error": body.get("error") or {"code": f"HTTP_{res.status_code}", "message": res.text[:200]}}
                transient = res.status_code in TRANSIENT

        ms = int((time.monotonic() - started) * 1000)
        if outcome["ok"]:
            trace.record(trace.Actor.SYSTEM, "api_ok", f"{method} {path} → OK ({ms} ms)",
                         run_id=run_id, order_id=order_id)
            return outcome
        err = outcome["error"]
        trace.record(trace.Actor.SYSTEM, "api_error",
                     f"{method} {path} → {err.get('code')}: {err.get('message')} ({ms} ms, pokus {attempt})",
                     run_id=run_id, order_id=order_id)
        if not transient or attempt > MAX_RETRIES:
            outcome["attempts"] = attempt
            return outcome
        backoff = 0.5 * 2 ** (attempt - 1)
        trace.record(trace.Actor.SCRIPT, "api_retry",
                     f"Přechodná chyba – opakuji za {backoff:.1f} s (pokus {attempt + 1}/{MAX_RETRIES + 1})",
                     run_id=run_id, order_id=order_id)
        time.sleep(backoff)
    return outcome  # pro typovou kontrolu; cyklus vždy vrátí dřív


def _json(res: requests.Response) -> dict:
    try:
        return res.json()
    except ValueError:
        return {}
