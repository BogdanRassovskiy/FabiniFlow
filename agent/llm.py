"""
Klient pro OpenRouter (OpenAI-kompatibilní Chat Completions API s tool calling).

- Přechodné chyby (síť, timeout, 429, 5xx) zkouší znovu s backoffem.
- Seznam modelů pro UI filtruje jen ty, které umí nástroje (`tools`).
- Vybraný model se ukládá do JSON nastavení, takže ho lze měnit z UI za běhu.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import requests
from django.conf import settings

from core import storage

SETTINGS = "settings"
_models_cache: dict[str, Any] = {"at": 0.0, "models": []}


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def chat(self, *, model: str, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        """Vrací {"message": {...}, "usage": {...}}."""


class OpenRouterClient:
    timeout = 60
    max_retries = 2

    def chat(self, *, model: str, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if not settings.OPENROUTER_API_KEY:
            raise LLMError("Chybí OPENROUTER_API_KEY (viz .env)")
        payload = {"model": model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "temperature": 0.2, "max_tokens": 1500}
        headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                   "HTTP-Referer": "http://localhost:8000", "X-Title": "FabiniFlow"}
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                res = requests.post(f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                                    json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"síťová chyba: {exc.__class__.__name__}"
            else:
                if res.status_code == 200:
                    body = res.json()
                    if body.get("error"):
                        last_error = f"chyba poskytovatele: {body['error'].get('message')}"
                    elif body.get("choices"):
                        return {"message": body["choices"][0]["message"], "usage": body.get("usage") or {}}
                    else:
                        last_error = "prázdná odpověď modelu"
                elif res.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {res.status_code}"
                else:
                    raise LLMError(f"HTTP {res.status_code}: {res.text[:300]}")
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"Model neodpověděl ani po {self.max_retries + 1} pokusech ({last_error})")


def current_model() -> str:
    record = storage.find(SETTINGS, "key", "model")
    return (record or {}).get("value") or settings.OPENROUTER_DEFAULT_MODEL


def set_model(model_id: str) -> None:
    with storage.transaction(SETTINGS) as records:
        records[:] = [r for r in records if r["key"] != "model"]
        records.append({"key": "model", "value": model_id})


def available_models() -> list[dict[str, Any]]:
    """Modely s podporou nástrojů (cache 1 h). Při výpadku vrátí aspoň doporučené."""
    if time.time() - _models_cache["at"] < 3600 and _models_cache["models"]:
        return _models_cache["models"]
    recommended = {m["id"] for m in settings.OPENROUTER_RECOMMENDED_MODELS}
    try:
        data = requests.get(f"{settings.OPENROUTER_BASE_URL}/models", timeout=10).json()["data"]
    except (requests.RequestException, ValueError, KeyError):
        return [{**m, "recommended": True, "vision": True} for m in settings.OPENROUTER_RECOMMENDED_MODELS]

    models = []
    for m in data:
        if "tools" not in (m.get("supported_parameters") or []):
            continue
        pricing = m.get("pricing") or {}
        try:
            price_in = float(pricing.get("prompt", 0)) * 1e6
            price_out = float(pricing.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            continue
        models.append({
            "id": m["id"], "label": m.get("name", m["id"]),
            "price": f"{price_in:.2f} / {price_out:.2f}", "price_in": price_in,
            "vision": "image" in ((m.get("architecture") or {}).get("input_modalities") or []),
            "recommended": m["id"] in recommended,
        })
    models.sort(key=lambda m: (not m["recommended"], m["price_in"]))
    _models_cache.update(at=time.time(), models=models)
    return models
