from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from agent import approvals, attachments, conversations, handoff, llm, request_types, runner
from core import storage, trace
from shops import repository
from simulator import faults


def index(request):
    return redirect("web:shop_admin")


# Ukázkové zprávy pro demo – pokrývají hlavní scénáře v seed datech.
SAMPLE_MESSAGES = {
    "fabini_cz": [
        "Dobrý den, objednávku 100245 jsem zaplatil kartou, ale pořád nebyla odeslána. Co se děje?",
        "Proč ještě nebyla odeslána moje objednávka 100246? Pánve potřebuju do pátku.",
        "Objednávka 100248 – kdy dorazí ten litinový hrnec Mattia?",
    ],
    "fabini_sk": [
        "Dobrý deň, objednávku 100247 som zaplatil už predvčerom a stále nič. Môžete to preveriť?",
        "Kde je moja objednávka 100244?",
    ],
}

# Ukázkové provozní požadavky pro konzoli agenta v administraci.
AGENT_EXAMPLES = [
    "Zjisti, proč objednávka 100245 nebyla expedována, a pokud je problém v synchronizaci, navrhni další postup.",
    "Proč objednávka 100247 stále není ve skladu? Vyřeš to.",
    "Zákazník se ptá na objednávku 100248 – kdy bude odeslána?",
    "Zkontroluj objednávku 100999.",
]

# Připravené snímky obrazovky pro simulaci příloh (zdroj: docs/screenshots-src).

@ensure_csrf_cookie
def customer(request):
    shops = repository.shops()
    shop_id = request.GET.get("shop") or shops[0]["shop_id"]
    return render(request, "web/customer.html", {
        "tab": "customer",
        "shops": shops,
        "shop": _shop_or_404(shop_id),
        "sample_messages": SAMPLE_MESSAGES.get(shop_id, []),
        "sample_screenshots": attachments.for_shop(shop_id),
        "request_types": request_types.as_dict(),
    })


def shop_admin(request):
    shops = repository.shops()
    shop_id = request.GET.get("shop") or shops[0]["shop_id"]
    shop = _shop_or_404(shop_id)
    warehouse = repository.warehouse(shop["warehouse_id"])
    products = {p["sku"]: p for p in repository.products(shop_id)}
    stock = [
        {"sku": sku, "name": products.get(sku, {}).get("name", sku), "qty": qty}
        for sku, qty in warehouse["stock"].items()
    ]
    return render(request, "web/shop_admin.html", {
        "tab": "shop_admin",
        "subtab": "shop",
        "shops": shops,
        "shop": shop,
        "orders": repository.order_board(shop_id),
        "warehouse": warehouse,
        "stock": stock,
        "tickets": storage.list_all("tickets", lambda t: t.get("shop_id") == shop_id),
        "attention": approvals.pending(shop_id),
        "agent_examples": AGENT_EXAMPLES,
        "requests": sorted(storage.list_all("requests", lambda r: r["shop_id"] == shop_id),
                           key=lambda r: r["created_at"], reverse=True),
        "live_chats": storage.list_all(
            "conversations", lambda c: c["shop_id"] == shop_id and c.get("mode") == handoff.HUMAN),
        "error": request.GET.get("error"),
    })


@require_POST
def decide_approval(request, item_id):
    shop_id = request.POST.get("shop", "")
    url = f"{reverse('web:shop_admin')}?shop={shop_id}"
    try:
        item = approvals.decide(item_id, request.POST.get("decision", ""),
                                note=request.POST.get("note", "").strip() or None)
    except (ValueError, storage.NotFound) as exc:
        return redirect(f"{url}&error={exc}")
    if item["kind"] == "handoff" and item["status"] == approvals.APPROVED:
        return redirect("web:operator_chat", conversation_id=item["conversation_id"])
    return redirect(url)


@require_POST
def chat_start(request):
    try:
        conv = conversations.start(request.POST.get("shop", ""))
    except storage.NotFound:
        raise Http404("Obchod neexistuje")
    return JsonResponse({"conversation_id": conv["conversation_id"]}, status=201)


@require_GET
def chat_state(request, conversation_id):
    """Stav chatu pro zákazníka – bez prozrazení čekající žádosti o předání člověku."""
    conv = _conversation_or_404(conversation_id)
    return JsonResponse(_chat_payload(conv, handoff.customer_mode(conv)))


@require_GET
def operator_chat_state(request, conversation_id):
    """Úplný stav chatu pro administraci (včetně režimu handoff_pending)."""
    conv = _conversation_or_404(conversation_id)
    return JsonResponse(_chat_payload(conv, handoff.mode(conv)))


def _chat_payload(conv, mode):
    typing = mode == handoff.AGENT and any(
        r["status"] == runner.RUNNING
        for r in storage.list_all("runs", lambda r: r.get("conversation_id") == conv["conversation_id"]))
    return {
        "conversation_id": conv["conversation_id"],
        "shop_id": conv["shop_id"],
        "mode": mode,
        "typing": typing,
        "messages": [{**m, "attachment_url": attachments.url(m["attachment"]) if m.get("attachment") else None}
                     for m in conv["messages"]],
        "draft": conversations.draft_summary(conv),
    }


@require_POST
def agent_run_start(request):
    text = request.POST.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "Zadejte požadavek"}, status=400)
    run = runner.start_ops_run(text[:2000], shop_id=request.POST.get("shop") or None)
    return JsonResponse({"run_id": run["run_id"]}, status=201)


@require_GET
def agent_run_state(request, run_id):
    try:
        run = runner.get(run_id)
    except storage.NotFound:
        raise Http404("Běh neexistuje")
    return JsonResponse({
        "run_id": run_id,
        "status": run["status"],
        "model": run["model"],
        "llm_calls": run["llm_calls"],
        "usage": run["usage"],
        "input": run.get("input"),
        "result": run["result"],
        "error": run["error"],
        "pending": run["pending"],
        "trace": list(reversed(trace.entries(run_id=run_id))),
    })


def models_api(request):
    if request.method == "POST":
        model = request.POST.get("model", "").strip()
        if model:
            llm.set_model(model)
            trace.record(trace.Actor.HUMAN, "model_changed", f"Model agenta změněn na {model}")
        return JsonResponse({"current": llm.current_model()})
    return JsonResponse({"current": llm.current_model(), "has_key": bool(settings.OPENROUTER_API_KEY),
                         "models": llm.available_models()})


@require_POST
def chat_message(request, conversation_id):
    """Zpráva zákazníka (volitelně se snímkem). Odpověď agenta doplní běh agenta (agent loop)."""
    conv = _conversation_or_404(conversation_id)
    text = request.POST.get("text", "").strip()
    file = request.POST.get("attachment") or None
    if file and not attachments.get(file, conv["shop_id"]):
        return JsonResponse({"error": "Neznámá příloha"}, status=400)
    if not text and not file:
        return JsonResponse({"error": "Prázdná zpráva"}, status=400)
    conversations.add_message(conversation_id, "customer", text[:2000], attachment=file)
    runner.start_customer_run(conversation_id)
    return JsonResponse({"ok": True})


@require_POST
def operator_message(request, conversation_id):
    _conversation_or_404(conversation_id)
    try:
        handoff.operator_message(conversation_id, request.POST.get("text", "")[:2000])
    except conversations.DraftError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"ok": True})


def operator_chat(request, conversation_id):
    conv = _conversation_or_404(conversation_id)
    if request.method == "POST":
        try:
            handoff.return_to_agent(conversation_id)
        except conversations.DraftError:
            pass
        return redirect(f"{reverse('web:shop_admin')}?shop={conv['shop_id']}")
    return render(request, "web/operator_chat.html", {
        "tab": "shop_admin",
        "subtab": "shop",
        "conv": conv,
        "shop": repository.shop(conv["shop_id"]),
        "handoff": conv.get("handoff") or {},
    })


def _conversation_or_404(conversation_id):
    try:
        return conversations.get(conversation_id)
    except storage.NotFound:
        raise Http404("Konverzace neexistuje")


@require_GET
def attention_api(request):
    """Počty čekajících položek – UI je periodicky načítá kvůli odznaku na záložce."""
    return JsonResponse(approvals.pending_counts())


def simulation(request):
    if request.method == "POST":
        if request.POST.get("action") == "reset_data":
            storage.reset_from_seed()
            trace.record(trace.Actor.HUMAN, "data_reset", "Administrátor obnovil výchozí data všech systémů")
            return redirect("web:simulation")

        for config in faults.all_configs():
            system = config["system"]
            new = {
                "mode": request.POST.get(f"{system}-mode", "ok"),
                "latency_ms": int(request.POST.get(f"{system}-latency_ms") or 0),
                "order_ids": [o.strip() for o in request.POST.get(f"{system}-order_ids", "").split(",") if o.strip()],
            }
            if any(config[k] != v for k, v in new.items()):
                faults.update_config(system, **new)
                scope = ", ".join(new["order_ids"]) or "všechny objednávky"
                trace.record(trace.Actor.HUMAN, "simulation_changed",
                             f"{faults.SYSTEMS[system]}: {faults.MODES[new['mode']]}, "
                             f"zpoždění {new['latency_ms']} ms, rozsah: {scope}",
                             data={"system": system, **new})
        return redirect("web:simulation")

    return render(request, "web/simulation.html", {
        "tab": "shop_admin",
        "subtab": "simulation",
        "configs": [{**c, "label": faults.SYSTEMS[c["system"]]} for c in faults.all_configs()],
        "modes": faults.MODES,
    })


def trace_log(request):
    actor = request.GET.get("actor") or None
    entries = trace.entries(
        run_id=request.GET.get("run") or None,
        order_id=request.GET.get("order") or None,
    )
    if actor:
        entries = [e for e in entries if e["actor"] == actor]
    return render(request, "web/trace.html", {
        "tab": "trace",
        "entries": entries,
        "actors": [a.value for a in trace.Actor],
        "actor": actor,
    })


def _shop_or_404(shop_id):
    try:
        return repository.shop(shop_id)
    except storage.NotFound:
        raise Http404("Obchod neexistuje")
