"""Independent plan checker. Must NOT import OR-Tools.

It re-checks every rule the CP-SAT model is supposed to enforce, using plain
Python, so a bug in the model cannot silently reach the baker.
Returns a list of human-readable violations (empty list = valid).
"""

from __future__ import annotations

from .types import ScheduleInput, ScheduleResult


def _hhmm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def validate(inp: ScheduleInput, res: ScheduleResult, *, now: int | None = None) -> list[str]:
    """Check ``res`` against ``inp``.

    ``now`` lets the API re-check an old proposal at approval time: any step
    that has not started yet must not be planned before the current time.
    """
    v: list[str] = []
    if res.status != "feasible":
        return v
    steps = inp.all_steps()
    sched = res.steps
    clock = inp.now if now is None else now

    missing = set(steps) - set(sched)
    if missing:
        return [f"missing steps: {sorted(missing)}"]

    def frozen(sid: str) -> bool:
        return sid in inp.frozen

    # Sizes, frozen facts, day window, "not in the past".
    for sid, st in steps.items():
        s = sched[sid]
        if s.end - s.start != inp.size_of(sid):
            v.append(f"{sid}: length {s.end - s.start} != {inp.size_of(sid)}")
        f = inp.frozen.get(sid)
        if f is not None:
            if s.start != f.start or (f.end is not None and s.end != f.end):
                v.append(f"{sid}: changed a step that already happened")
            continue
        if s.start < max(inp.day_start, clock):
            v.append(f"{sid}: starts {_hhmm(s.start)}, before {_hhmm(max(inp.day_start, clock))}")
        if s.end > inp.day_end:
            v.append(f"{sid}: ends {_hhmm(s.end)}, after day end {_hhmm(inp.day_end)}")

    def check_chain(ids: list[str], waits: list[int | None]) -> None:
        for (a, b), w in zip(zip(ids, ids[1:]), waits):
            if frozen(a) and frozen(b):
                continue
            gap = sched[b].start - sched[a].end
            if gap < 0:
                v.append(f"{b} starts before {a} ends")
            elif w is not None and gap > w:
                v.append(f"{b} waits {gap} min after {a} (max {w})")

    for b in inp.batches:
        check_chain([s.id for s in b.steps], list(b.waits))
    for t in inp.trays:
        check_chain([s.id for s in t.steps], list(t.waits))

    for b in inp.batches:
        last = b.steps[-1].id
        trays = [t for t in inp.trays if t.batch_id == b.id]
        window = sum(inp.size_of(t.steps[0].id) for t in trays) + b.shape_slack
        for t in trays:
            first = t.steps[0].id
            if frozen(first) and frozen(last):
                continue
            if sched[first].start < sched[last].end:
                v.append(f"{first} starts before batch {b.id} is ready")
            if not frozen(first) and sched[first].end - sched[last].end > window:
                v.append(f"{first}: batch {b.id} not shaped within {window} min")

    tray_by_id = {t.id: t for t in inp.trays}
    for o in inp.orders:
        pk = o.package.id
        for tid in o.tray_ids:
            last = tray_by_id[tid].steps[-1].id
            if frozen(last) and frozen(pk):
                continue
            if sched[pk].start < sched[last].end:
                v.append(f"order {o.id} packed before tray {tid} is done")
        ready = sched[pk].end
        if res.order_ready.get(o.id) != ready:
            v.append(f"order {o.id}: reported ready time is wrong")
        if o.promise_ready_by is not None and not frozen(pk) and ready > o.promise_ready_by:
            v.append(f"order {o.id}: breaks promise {_hhmm(o.promise_ready_by)}")
    if inp.request is not None:
        r = inp.request
        if sched[next(o.package.id for o in inp.orders if o.id == r.order_id)].end > r.ready_by:
            v.append(f"order {r.order_id}: misses requested {_hhmm(r.ready_by)}")

    # Resources: sweep every minute boundary where something starts.
    by_where: dict[str, list[str]] = {}
    for sid, st in steps.items():
        by_where.setdefault(st.where, []).append(sid)

    person = sorted(by_where.get("person", []), key=lambda s: sched[s].start)
    for a, b in zip(person, person[1:]):
        if sched[b].start < sched[a].end:
            v.append(f"person busy twice: {a} and {b}")

    def check_capacity(ids: list[str], demand, cap: int, label: str) -> None:
        for sid in ids:
            t = sched[sid].start
            used = sum(demand(x) for x in ids if sched[x].start <= t < sched[x].end)
            if used > cap:
                v.append(f"{label} over capacity at {_hhmm(t)}: {used} > {cap}")

    proof = by_where.get("oven_proof", [])
    bake = by_where.get("oven_bake", [])
    check_capacity(proof, lambda s: steps[s].demand, inp.oven.proof_slots, "oven (proof)")
    check_capacity(bake, lambda s: 1, inp.oven.bake_trays, "oven (bake)")

    for p in proof:
        for b in bake:
            if frozen(p) and frozen(b):
                continue
            P, B = sched[p], sched[b]
            ok = (P.end + inp.oven.switch_proof_to_bake <= B.start) or (
                B.end + inp.oven.switch_bake_to_proof <= P.start
            )
            if not ok:
                v.append(f"oven: {p} (proof) and {b} (bake) too close or overlapping")

    if res.day_finish != max(s.end for s in sched.values()):
        v.append("reported day finish is wrong")
    return v
