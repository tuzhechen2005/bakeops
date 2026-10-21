"""T2 risk check: can CP-SAT plan a day with one person and one oven that is
also the proofer, and refuse a request it cannot meet?

Run:  python spikes/t2_scheduler.py
All numbers come from config/kitchen.example.yaml (test values, not advice).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from bakeops.scheduler import earliest_ready, solve  # noqa: E402
from bakeops.scheduler.builder import (  # noqa: E402
    BatchLayout,
    OrderLayout,
    TrayLayout,
    build_input,
    load_config,
    min_to_hhmm,
    unconfirmed,
)
from bakeops.scheduler.types import Frozen  # noqa: E402

CFG = load_config(ROOT / "config" / "kitchen.example.yaml")

BATCHES = [
    BatchLayout(id="B1", trays=[TrayLayout(id="T1", product_id="red_bean"), TrayLayout(id="T2", product_id="cheese")]),
    BatchLayout(id="B2", trays=[TrayLayout(id="T3", product_id="pork_floss"), TrayLayout(id="T4", product_id="milk_roll")]),
]
ORDERS = [
    OrderLayout(id="zhang", tray_ids=["T1", "T2"]),  # 张三: red bean + cheese
    OrderLayout(id="wang", tray_ids=["T3"]),          # 王五: pork floss
    OrderLayout(id="zhao", tray_ids=["T4"]),          # 赵阿姨: milk rolls
]

LANE = {"person": "You   ", "oven_proof": "Oven:P", "oven_bake": "Oven:B", "room": "Bench "}


def show(res, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"status={res.status} solver={res.solver_status} optimal={res.optimal}")
    if res.status != "feasible":
        return
    for s in sorted(res.steps.values(), key=lambda s: (s.start, s.id)):
        print(f"  {LANE[s.where]}  {min_to_hhmm(s.start)}-{min_to_hhmm(s.end)}  {s.id}")
    print("  ready:", {k: min_to_hhmm(v) for k, v in res.order_ready.items()})
    print("  day finishes:", min_to_hhmm(res.day_finish))


def main() -> None:
    print("Unconfirmed numbers in config:", ", ".join(unconfirmed(CFG)) or "none")

    # 1. Morning plan.
    t = time.perf_counter()
    inp = build_input(CFG, BATCHES, ORDERS, now="06:00")
    plan = solve(inp)
    show(plan, f"1. Morning plan ({time.perf_counter() - t:.2f}s)")

    # 2. Mid-day: everything that should have started by 08:00 has started.
    #    Wang asks for 09:30. Expect: refuse, and say the earliest time.
    now = 8 * 60
    frozen = {
        sid: Frozen(start=s.start, end=s.end if s.end <= now else None)
        for sid, s in plan.steps.items()
        if s.start <= now
    }
    t = time.perf_counter()
    req = build_input(
        CFG, BATCHES, ORDERS, now="08:00", frozen=frozen,
        previous_ready=plan.order_ready, request=("wang", "09:30"), objective="minimize_delay",
    )
    res = solve(req)
    show(res, f"2. Wang wants 09:30 ({time.perf_counter() - t:.2f}s)")
    if res.status == "infeasible":
        e = earliest_ready(req, "wang")
        print(f"  -> cannot do 09:30. earliest: {e.status} {min_to_hhmm(e.ready) if e.ready else '-'} proven={e.proven}")

    # 3. Before anything started, Zhang asks for 10:30. Nobody has a promise
    #    yet, so the planner may swap the two batches. Expect: yes, others move.
    def ask_zhang(promise_wang: bool, title: str) -> None:
        orders = [
            o.model_copy(update={"promise_ready_by": min_to_hhmm(plan.order_ready["wang"])})
            if (o.id == "wang" and promise_wang) else o
            for o in ORDERS
        ]
        req = build_input(
            CFG, BATCHES, orders, now="06:00",
            previous_ready=plan.order_ready, request=("zhang", "10:30"), objective="minimize_delay",
        )
        t = time.perf_counter()
        res = solve(req)
        show(res, f"{title} ({time.perf_counter() - t:.2f}s)")
        if res.status == "feasible":
            for oid, before in plan.order_ready.items():
                after = res.order_ready[oid]
                if after != before:
                    print(f"  diff: {oid} {min_to_hhmm(before)} -> {min_to_hhmm(after)} ({after - before:+d} min)")
        else:
            e = earliest_ready(req, "zhang")
            print(f"  -> earliest for zhang: {e.status} {min_to_hhmm(e.ready) if e.ready else '-'} proven={e.proven}")

    ask_zhang(False, "3. Zhang wants 10:30, nobody promised yet")
    # 4. Same request, but Wang was already told 09:43. Expect: no.
    ask_zhang(True, f"4. Zhang wants 10:30, Wang already promised {min_to_hhmm(plan.order_ready['wang'])}")


if __name__ == "__main__":
    main()
