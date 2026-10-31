"""
Agent loop: LLM → volání nástrojů → výsledky → LLM … až po výsledek.

Rozdělení odpovědností (viditelné i v trace logu):
- LLM rozhoduje, které nástroje zavolat a jaký je závěr;
- kód (SCRIPT) hlídá mantinely: limit kroků, limit volání nástroje,
  deduplikaci, dostupnost nástroje v kanálu, schvalování rizikových akcí;
- člověk (HUMAN) schvaluje / zamítá rizikové akce.

Běh se ukládá do runs.json po každém kroku, takže se může zastavit na
schválení a po rozhodnutí člověka pokračovat přesně tam, kde skončil.
Běhy jedou ve vlákně na pozadí (UI zatím zobrazuje průběh z trace).
"""

from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from typing import Any

from django.conf import settings
from django.utils import timezone

from agent import approvals, conversations, handoff, llm, prompts, tools
from core import storage, trace

COLLECTION = "runs"
MAX_LLM_CALLS = 12
MAX_TOOL_CALLS = 30

RUNNING, AWAITING, COMPLETED, FAILED = "running", "awaiting_approval", "completed", "failed"

client: llm.LLMClient = llm.OpenRouterClient()

_TEXTS = {
    "cs": {"holding": "Děkuji, ověřuji to s kolegy a hned se vám ozvu.",
           "failure": "Omlouváme se, momentálně máme technické potíže. Ozveme se vám co nejdříve."},
    "sk": {"holding": "Ďakujem, overujem to s kolegami a hneď sa vám ozvem.",
           "failure": "Ospravedlňujeme sa, momentálne máme technické problémy. Ozveme sa vám čo najskôr."},
}


def _now() -> str:
    return timezone.localtime().isoformat(timespec="seconds")


# --- Spuštění -----------------------------------------------------------------

def start_ops_run(text: str, *, shop_id: str | None = None) -> dict[str, Any]:
    run = _create("ops", shop_id=shop_id, input=text)
    run["messages"] = [{"role": "user", "content": text}]
    _save(run)
    trace.record(trace.Actor.HUMAN, "run_started", f"Požadavek pro agenta: {text}", run_id=run["run_id"])
    _spawn(_loop, run["run_id"])
    return run


@storage.atomic  # kontrola „neběží už jiný běh?“ a založení nového běhu musí být jeden krok
def start_customer_run(conversation_id: str) -> dict[str, Any] | None:
    conv = conversations.get(conversation_id)
    if handoff.mode(conv) != handoff.AGENT:
        return None  # chat vede (nebo má převzít) člověk – agent mlčí
    active = [r for r in storage.list_all(COLLECTION, lambda r: r.get("conversation_id") == conversation_id)
              if r["status"] in (RUNNING, AWAITING)]
    if active:
        # Zákazník napsal během běhu – běžící agent zprávu uvidí v dalším kroku,
        # a pokud na ni do konce běhu neodpoví, spustí se nový běh (_followup).
        return None
    run = _create("customer", shop_id=conv["shop_id"], conversation_id=conversation_id,
                  input=(conv["messages"][-1]["text"] if conv["messages"] else ""),
                  seen_messages=len(conv["messages"]))
    _spawn(_loop, run["run_id"])
    return run


def resume_after_approval(run_id: str, approved: bool, note: str | None) -> None:
    _spawn(_resume, run_id, approved, note)


def _create(channel: str, **fields: Any) -> dict[str, Any]:
    run = {
        "run_id": f"run-{uuid.uuid4().hex[:6]}",
        "channel": channel,
        "model": llm.current_model(),
        "status": RUNNING,
        "created_at": _now(),
        "updated_at": _now(),
        "llm_calls": 0,
        "messages": [],
        "queue": [],
        "pending": None,
        "tool_log": [],
        "call_counts": {},
        "cache": {},
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "result": None,
        "error": None,
        "nudged": False,
        "followup": False,
        **fields,
    }
    return storage.insert(COLLECTION, run)


def get(run_id: str) -> dict[str, Any]:
    return storage.get(COLLECTION, "run_id", run_id)


def _save(run: dict[str, Any]) -> None:
    run["updated_at"] = _now()
    storage.update(COLLECTION, "run_id", run["run_id"], run)


def _spawn(fn, *args) -> None:
    if getattr(settings, "AGENT_SYNC", False):
        fn(*args)
    else:
        threading.Thread(target=fn, args=args, daemon=True).start()


# --- Smyčka -------------------------------------------------------------------

def _loop(run_id: str) -> None:
    try:
        _loop_inner(run_id)
    except Exception as exc:  # poslední záchrana – běh nesmí „viset“ ve stavu running
        _fail(get(run_id), f"Neočekávaná chyba: {exc.__class__.__name__}: {exc}", detail=traceback.format_exc())


def _customer_history(run: dict[str, Any]) -> list[dict]:
    vision = llm.supports_vision(run["model"])
    run["history_len"] = len(conversations.get(run["conversation_id"])["messages"])
    if run["llm_calls"] == 0:
        last = next((m for m in reversed(conversations.get(run["conversation_id"])["messages"])
                     if m["role"] == "customer"), None)
        if last and last.get("attachment"):
            if vision:
                trace.record(trace.Actor.SCRIPT, "attachment_sent",
                             f"Snímek {last['attachment']} předán modelu {run['model']} jako obrázek", run_id=run["run_id"])
            else:
                trace.record(trace.Actor.SCRIPT, "attachment_skipped",
                             f"Model {run['model']} neumí obrázky – snímek {last['attachment']} nahrazen poznámkou",
                             run_id=run["run_id"])
    return prompts.customer_history(run["conversation_id"], vision=vision)


def _loop_inner(run_id: str) -> None:
    run = get(run_id)
    while run["status"] == RUNNING:
        if run["llm_calls"] >= MAX_LLM_CALLS:
            trace.record(trace.Actor.SCRIPT, "step_limit",
                         f"Dosažen limit {MAX_LLM_CALLS} kroků – běh zastaven (ochrana proti zacyklení)", run_id=run_id)
            _fail(run, f"Překročen limit {MAX_LLM_CALLS} kroků")
            return

        ctx = _ctx(run)
        system = prompts.system_prompt(run["channel"], shop_id=run.get("shop_id"),
                                       conversation_id=run.get("conversation_id"))
        history = (_customer_history(run) if run["channel"] == tools.CUSTOMER else [])
        try:
            response = client.chat(model=run["model"],
                                   messages=[{"role": "system", "content": system}, *history, *run["messages"]],
                                   tools=[t.schema() for t in tools.for_channel(ctx.channel)])
        except llm.LLMError as exc:
            trace.record(trace.Actor.SYSTEM, "llm_error", f"Model {run['model']}: {exc}", run_id=run_id)
            _fail(run, str(exc))
            return

        run["llm_calls"] += 1
        for k in ("prompt_tokens", "completion_tokens"):
            run["usage"][k] += response["usage"].get(k) or 0
        message = response["message"]
        tool_calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()
        run["messages"].append({"role": "assistant", "content": message.get("content"),
                                **({"tool_calls": tool_calls} if tool_calls else {})})
        if content and tool_calls:
            trace.record(trace.Actor.LLM, "reasoning", content[:500], run_id=run_id)

        if not tool_calls:
            _final_text(run, content)
            return

        run["queue"] = [dict(c) for c in tool_calls]  # kopie – fronta se vyprazdňuje, historie ne
        _save(run)
        if _process_queue(run) != "continue":
            return
        run = get(run_id)


def _final_text(run: dict[str, Any], content: str) -> None:
    run_id = run["run_id"]
    if run["channel"] == tools.OPS:
        if not run["nudged"]:
            run["nudged"] = True
            run["messages"].append({"role": "user", "content": "Ukonči běh voláním submit_result se strukturovaným výsledkem."})
            trace.record(trace.Actor.SCRIPT, "result_required",
                         "Model odpověděl textem – vyžádán strukturovaný výsledek přes submit_result", run_id=run_id)
            _save(run)
            _loop_inner(run_id)
            return
        _complete(run, {"status": "failed", "summary": content[:500], "root_cause": "–",
                        "recommendation": "Model nevrátil strukturovaný výsledek.", "actions_taken": []})
        return

    conv = conversations.get(run["conversation_id"])
    unseen = conv["messages"][run.get("history_len", len(conv["messages"])):]
    if content and handoff.mode(conv) == handoff.AGENT and any(m["role"] == "customer" for m in unseen):
        # Zákazník mezitím napsal další zprávu – odpověď by ji ignorovala, model ji přepíše s novou historií.
        trace.record(trace.Actor.SCRIPT, "reply_outdated",
                     "Zákazník mezitím napsal další zprávu – odpověď se neodešle, agent odpoví znovu s aktuální historií",
                     run_id=run_id)
        run["messages"].pop()  # zahodit zastaralou odpověď z kontextu běhu
        _save(run)
        _loop_inner(run_id)
        return
    if handoff.mode(conv) != handoff.AGENT:
        trace.record(trace.Actor.SCRIPT, "reply_suppressed",
                     "Odpověď agenta zákazníkovi potlačena – chat čeká na člověka / vede ho člověk", run_id=run_id)
    elif content:
        conversations.add_message(run["conversation_id"], "agent", content)
        trace.record(trace.Actor.LLM, "reply", f"Odpověď zákazníkovi: {content[:300]}", run_id=run_id,
                     order_id=conversations.order_id_of(conv))
    _complete(run, {"reply": content})


def _process_queue(run: dict[str, Any]) -> str:
    """Zpracuje čekající volání nástrojů. Vrací continue | paused | finished."""
    while run["queue"]:
        call = run["queue"][0]
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = None
        tool = tools.TOOLS.get(name)
        if isinstance(args, dict):
            args = _repair_args(run, name, args)
        trace.record(trace.Actor.LLM, "tool_call", f"{name}({_fmt(args)})", run_id=run["run_id"],
                     order_id=(args or {}).get("order_id"))

        result = _guard(run, tool, name, args)
        if result is None and tool.risk == tools.RISKY:
            _pause_for_approval(run, call, name, args)
            return "paused"
        if result is None:
            result = _execute(run, tool, args)

        _tool_result(run, call, name, args, result)
        if name == "submit_result" and result.get("ok"):
            actions = [f"{l['tool']}({_fmt(l['args'])}) → {l['outcome']}" for l in run["tool_log"]
                       if tools.TOOLS.get(l["tool"]) and tools.TOOLS[l["tool"]].risk == tools.RISKY]
            _complete(run, {**args, "actions_taken": actions, "steps": run["tool_log"]})
            return "finished"
        if name == "request_human_handoff" and result.get("ok"):
            trace.record(trace.Actor.SCRIPT, "agent_silent",
                         "Po žádosti o předání agent zákazníkovi neodpovídá, dokud nerozhodne člověk", run_id=run["run_id"])
            _complete(run, {"handoff_requested": True})
            return "finished"
    _save(run)
    return "continue"


def _guard(run, tool, name, args) -> dict[str, Any] | None:
    """Mantinely v kódu. Vrací výsledek (chybu / cache), nebo None = smí se provést."""
    def blocked(code, message):
        trace.record(trace.Actor.SCRIPT, "guard_blocked", f"{name}: {message}", run_id=run["run_id"])
        return {"ok": False, "error": {"code": code, "message": message}}

    if tool is None or run["channel"] not in tool.channels:
        return blocked("UNKNOWN_TOOL", f"Nástroj {name} v tomto kanálu neexistuje")
    if args is None:
        return blocked("INVALID_ARGS", "Argumenty nejsou platný JSON")
    if any("</parameter>" in str(v) or "<parameter" in str(v) for v in args.values()):
        return blocked("INVALID_ARGS", "Argumenty obsahují XML značky – pošli každý argument zvlášť jako čistý JSON")
    missing = [k for k in tool.parameters.get("required", []) if not args.get(k)]
    if missing:
        return blocked("INVALID_ARGS", f"Chybí povinné argumenty: {', '.join(missing)}")
    if sum(run["call_counts"].values()) >= MAX_TOOL_CALLS:
        return blocked("LIMIT", f"Překročen limit {MAX_TOOL_CALLS} volání nástrojů v jednom běhu")
    if run["call_counts"].get(name, 0) >= tool.max_calls:
        return blocked("LIMIT", f"Nástroj už byl v tomto běhu použit {tool.max_calls}× – další opakování není povoleno")
    key = _cache_key(name, args)
    if tool.dedupe and key in run["cache"]:
        trace.record(trace.Actor.SCRIPT, "duplicate_call",
                     f"{name}({_fmt(args)}) už v tomto běhu proběhlo – vrácen uložený výsledek", run_id=run["run_id"])
        return {**run["cache"][key], "cached": True}
    return None


def _execute(run, tool, args) -> dict[str, Any]:
    run["call_counts"][tool.name] = run["call_counts"].get(tool.name, 0) + 1
    try:
        result = tool.handler(_ctx(run), args)
    except Exception as exc:
        result = {"ok": False, "error": {"code": "TOOL_CRASH", "message": f"{exc.__class__.__name__}: {exc}"}}
    if tool.dedupe and result.get("ok"):
        run["cache"][_cache_key(tool.name, args)] = result
    if tool.risk == tools.RISKY and result.get("ok") and run["cache"]:
        # Akce změnila stav systémů – dřívější čtení už neplatí.
        run["cache"] = {}
        trace.record(trace.Actor.SCRIPT, "cache_invalidated",
                     f"{tool.name} změnil stav – uložené výsledky čtení zahozeny, další čtení půjde do systémů",
                     run_id=run["run_id"])
    return result


def _tool_result(run, call, name, args, result) -> None:
    run["queue"].pop(0)
    run["messages"].append({"role": "tool", "tool_call_id": call["id"],
                            "content": json.dumps(result, ensure_ascii=False)[:6000]})
    run["tool_log"].append({
        "n": len(run["tool_log"]) + 1, "tool": name, "args": args,
        "outcome": "cached" if result.get("cached") else ("ok" if result.get("ok") else "error"),
        "detail": (result.get("error") or {}).get("message") if not result.get("ok") else None,
    })
    _save(run)


# --- Schvalování ----------------------------------------------------------------

def _pause_for_approval(run, call, name, args) -> None:
    reason = args.get("reason", "")
    item = approvals.request_approval(
        tool=name, args={k: v for k, v in args.items() if k != "reason"},
        reason=reason or f"Agent navrhuje provést {name}.",
        shop_id=run.get("shop_id") or _shop_of_order(args.get("order_id")),
        order_id=args.get("order_id"), run_id=run["run_id"])
    run["status"] = AWAITING
    run["pending"] = {"approval_id": item["id"], "tool": name, "args": args}
    _save(run)
    if run["channel"] == tools.CUSTOMER:
        conversations.add_message(run["conversation_id"], "agent", _text(run, "holding"))


def _resume(run_id: str, approved: bool, note: str | None) -> None:
    try:
        run = get(run_id)
        if run["status"] != AWAITING:
            return
        call = run["queue"][0]
        name, args = run["pending"]["tool"], run["pending"]["args"]
        run["status"] = RUNNING
        run["pending"] = None
        if approved:
            result = _execute(run, tools.TOOLS[name], args)
        else:
            run["call_counts"][name] = run["call_counts"].get(name, 0) + 1
            result = {"ok": False, "error": {"code": "DENIED_BY_HUMAN",
                                             "message": "Člověk akci zamítl." + (f" Poznámka: {note}" if note else "")}}
        _tool_result(run, call, name, args, result)
        if _process_queue(run) == "continue":
            _loop_inner(run_id)
    except Exception as exc:
        _fail(get(run_id), f"Neočekávaná chyba: {exc.__class__.__name__}: {exc}", detail=traceback.format_exc())


# --- Konec běhu -------------------------------------------------------------------

def _complete(run, result) -> None:
    run["status"] = COMPLETED
    run["result"] = result
    _save(run)
    if run["channel"] == tools.OPS:
        trace.record(trace.Actor.LLM, "run_result",
                     f"Výsledek [{result.get('status')}]: {result.get('summary', '')}", run_id=run["run_id"],
                     order_id=result.get("order_id"))
    _followup(run)


def _fail(run, error: str, detail: str | None = None) -> None:
    run["status"] = FAILED
    run["error"] = error
    _save(run)
    approvals.raise_alert(title="Běh agenta selhal", reason=error,
                          shop_id=run.get("shop_id"), run_id=run["run_id"])
    if run["channel"] == tools.CUSTOMER:
        conversations.add_message(run["conversation_id"], "agent", _text(run, "failure"))


def _followup(run) -> None:
    """Zůstala-li po běhu poslední zpráva zákazníka bez odpovědi (napsal během běhu), odpoví nový běh."""
    if run["channel"] != tools.CUSTOMER:
        return
    messages = conversations.get(run["conversation_id"])["messages"]
    last = next((m for m in reversed(messages) if m["role"] != "system"), None)
    # Jen zprávy, které přišly až během tohoto běhu – jinak by prázdná odpověď modelu vedla ke smyčce.
    new = messages[run.get("seen_messages", len(messages)):]
    if last and last["role"] == "customer" and any(m["role"] == "customer" for m in new):
        start_customer_run(run["conversation_id"])


# --- Pomocné ------------------------------------------------------------------------

def _ctx(run) -> tools.RunContext:
    return tools.RunContext(run_id=run["run_id"], channel=run["channel"],
                            shop_id=run.get("shop_id"), conversation_id=run.get("conversation_id"))


_LEAKED_PARAM = re.compile(r'</parameter>\s*<parameter name="(\w+)">', re.S)


def _repair_args(run, name, args: dict[str, Any]) -> dict[str, Any]:
    """Některé modely „prosáknou“ XML formát volání do hodnoty argumentu
    (`text</parameter><parameter name="x">…`). Rozdělíme to zpět na samostatné argumenty."""
    repaired = dict(args)
    changed = False
    for key, value in args.items():
        if not isinstance(value, str) or "</parameter>" not in value:
            continue
        parts = _LEAKED_PARAM.split(value)
        repaired[key] = parts[0].strip()
        for extra_key, extra_value in zip(parts[1::2], parts[2::2]):
            extra_value = extra_value.replace("</parameter>", "").strip()
            try:
                repaired[extra_key] = json.loads(extra_value)
            except json.JSONDecodeError:
                repaired[extra_key] = extra_value
        changed = True
    if changed:
        trace.record(trace.Actor.SCRIPT, "args_repaired",
                     f"{name}: argumenty od modelu obsahovaly XML značky – rozděleny na samostatná pole",
                     run_id=run["run_id"])
    return repaired


def _cache_key(name, args) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _fmt(args) -> str:
    if not isinstance(args, dict):
        return "?"
    return ", ".join(f"{k}={v!r}" if len(str(v)) < 60 else f"{k}='{str(v)[:57]}…'" for k, v in args.items())


def _shop_of_order(order_id) -> str | None:
    order = storage.find("orders", "order_id", order_id) if order_id else None
    return order["shop_id"] if order else None


def _text(run, key) -> str:
    lang = storage.get("shops", "shop_id", run["shop_id"]).get("language", "cs") if run.get("shop_id") else "cs"
    return _TEXTS.get(lang, _TEXTS["cs"])[key]


def recent(limit: int = 10, channel: str | None = None) -> list[dict[str, Any]]:
    runs = storage.list_all(COLLECTION, lambda r: channel is None or r["channel"] == channel)
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)[:limit]
