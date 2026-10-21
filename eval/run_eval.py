"""Run the model eval.

    python eval/run_eval.py --model gemma4:e4b            # dev set
    python eval/run_eval.py --model gemma4:e4b --set holdout   # once, at the end

Prints a metrics table and writes per-item results to eval/results/.
Metrics are defined in docs/LLM_CONTRACT.md section 5.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from bakeops.llm.checks import CatalogItem, CustomerRef, OrderRef, check_message, check_orders  # noqa: E402
from bakeops.llm.interpreter import ModelFailed, OllamaInterpreter  # noqa: E402
from bakeops.llm.schemas import MessageInterpretation  # noqa: E402
from bakeops.scheduler.builder import hhmm_to_min, load_config  # noqa: E402

NEEDS_ORDER = {"ask_ready_time", "request_ready_by", "cancel", "change_quantity", "change_product"}


def load_context():
    cfg = load_config(ROOT / "config" / "kitchen.example.yaml")
    catalog = [CatalogItem(id=p["id"], name_zh=p["name_zh"], aliases=p.get("aliases", [])) for p in cfg["products"]]
    fx = json.loads((ROOT / "eval" / "fixtures.json").read_text(encoding="utf-8"))
    return catalog, fx


def message_payload(item, catalog, fx):
    c = fx["customers"][item["customer_id"]]
    return {
        "message": item["text"],
        "customer": {"id": item["customer_id"], "name": c["display_name"]},
        "orders": [{"id": o["id"], "summary": o["summary"], "ready_at": o.get("ready_at"), "promise": o.get("promise")} for o in c["orders"]],
        "items": [{"id": iid, "order_id": o["id"], "what": what} for o in c["orders"] for iid, what in o["items"].items()],
        "catalog": [p.model_dump() for p in catalog],
        "now": fx["now"], "open": fx["open"], "close": fx["close"],
    }


def score_parse(item, call, catalog):
    checked = check_orders(call.output, item["text"], catalog, [])
    got = [(o.raw_customer_text, {(i.product_id or "null"): i.qty for i in o.items}) for o in checked.orders]
    exp = [(o["customer"], o["items"]) for o in item["expected"]["orders"]]
    pool = list(got)
    correct = 0
    for name, items in exp:
        for k, (gname, gitems) in enumerate(pool):
            if gname == name:
                correct += gitems == items
                pool.pop(k)
                break
    return {
        "rows_expected": len(exp), "rows_correct": correct, "rows_extra": len(pool),
        "unparsed_ok": (len(checked.unparsed) > 0) == (item["expected"]["unparsed"] > 0),
        "problems": [p for o in checked.orders for p in o.problems + [x for i in o.items for x in i.problems]],
    }


def score_message(item, call, catalog, fx):
    e = item["expected"]
    out: MessageInterpretation = call.output
    c = fx["customers"][item["customer_id"]]
    orders = [OrderRef(id=o["id"], summary=o["summary"], ready_at=o.get("ready_at")) for o in c["orders"]]
    item_ids = {iid for o in c["orders"] for iid in o["items"]}
    checked = check_message(out, item["text"], orders, catalog, item_ids, hhmm_to_min(fx["open"]), hhmm_to_min(fx["close"]))
    needs_order = bool(set(e["intents"]) & NEEDS_ORDER)
    raw_wrong = needs_order and out.target_order_id is not None and out.target_order_id != e["target_order_id"]
    sys_wrong = needs_order and checked.target_order_id is not None and checked.target_order_id != e["target_order_id"]
    r = {
        "intent_ok": set(out.intents) == set(e["intents"]),
        "raw_wrong_order": raw_wrong,
        "system_wrong_order": sys_wrong,
        "ambiguous": e["needs_clarification"],
        # "Stopped" = nothing would happen without the baker answering first.
        "model_stopped": out.needs_clarification,
        "system_stopped": checked.needs_clarification or checked.must_pick_order or bool(checked.problems),
        "model_intents": out.intents,
        "questions": checked.questions,
        "problems": checked.problems,
    }
    if "requested_time_min" in e:
        r["time_ok"] = checked.requested_time_min == e["requested_time_min"]
        r["model_time_ok"] = out.requested_time_min == e["requested_time_min"]
    return r


def pct(n, d):
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="Ollama model tag, e.g. gemma4:e4b")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--set", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--think", choices=["on", "off"], default=None)
    ap.add_argument("--only", help="comma-separated item ids")
    args = ap.parse_args()
    catalog, fx = load_context()
    items = [json.loads(line) for line in (ROOT / "eval" / f"{args.set}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    think = None if args.think is None else args.think == "on"
    interp = OllamaInterpreter(args.model, host=args.host, think=think)
    name = args.model

    results = []
    for it in items:
        t0 = time.perf_counter()
        try:
            if it["task"] == "parse_orders":
                payload = {"text": it["text"], "catalog": [p.model_dump() for p in catalog], "customers": []}
                call = interp.parse_orders(payload)
                r = score_parse(it, call, catalog)
            else:
                call = interp.interpret_message(message_payload(it, catalog, fx))
                r = score_message(it, call, catalog, fx)
            r.update(failed=False, attempts=call.attempts, raw=call.raw)
        except ModelFailed as e:
            r = {"failed": True, "error": str(e)}
        r.update(id=it["id"], task=it["task"], seconds=round(time.perf_counter() - t0, 2))
        results.append(r)
        mark = "FAIL" if r["failed"] else "ok"
        print(f"  {it['id']:4} {mark:4} {r['seconds']:5.1f}s {it['text'][:30]}", flush=True)

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{name.replace(':', '_').replace('/', '_')}-{args.set}.jsonl"
    out_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")

    P = [r for r in results if r["task"] == "parse_orders"]
    M = [r for r in results if r["task"] == "interpret_message"]
    okP = [r for r in P if not r["failed"]]
    okM = [r for r in M if not r["failed"]]
    amb = [r for r in okM if r["ambiguous"]]
    timed = [r for r in okM if "time_ok" in r]
    print(f"\nModel: {name}   set: {args.set}   items: {len(results)}")
    print(f"  JSON failures (after 1 retry):        {sum(r['failed'] for r in results)}")
    print(f"  Order rows exactly right:             {pct(sum(r['rows_correct'] for r in okP), sum(r['rows_expected'] for r in okP))}   (target >= 90%)")
    print(f"  Unreadable lines flagged correctly:   {pct(sum(r['unparsed_ok'] for r in okP), len(okP))}")
    print(f"  Intent exactly right:                 {pct(sum(r['intent_ok'] for r in okM), len(okM))}   (target >= 85%)")
    print(f"  Stopped on ambiguity (model alone):   {pct(sum(r['model_stopped'] for r in amb), len(amb))}   (target >= 90%)")
    print(f"  Stopped on ambiguity (with checks):   {pct(sum(r['system_stopped'] for r in amb), len(amb))}")
    print(f"  Wrong order picked (model alone):     {sum(r['raw_wrong_order'] for r in okM)}")
    print(f"  Wrong order used (with checks):       {sum(r['system_wrong_order'] for r in okM)}   (must be 0)")
    print(f"  Time right (model's number):          {pct(sum(r.get('model_time_ok', False) for r in timed), len(timed))}")
    print(f"  Time right (Python re-parse):         {pct(sum(r['time_ok'] for r in timed), len(timed))}")
    if okM or okP:
        print(f"  Median seconds per item:              {sorted(r['seconds'] for r in results)[len(results) // 2]}")
    print(f"\nPer-item results: {out_file.relative_to(ROOT)}")
    look = [r["id"] for r in okM if not r["intent_ok"] or r["raw_wrong_order"] or (r["ambiguous"] and not r["model_stopped"])]
    look += [r["id"] for r in okP if r["rows_correct"] < r["rows_expected"] or r["rows_extra"] or not r["unparsed_ok"]]
    look += [r["id"] for r in results if r["failed"]]
    if look:
        print("Look at:", ", ".join(look))


if __name__ == "__main__":
    main()
