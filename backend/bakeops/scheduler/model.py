"""CP-SAT model for one baking day.

The solver only decides *when* each step starts. Which trays exist and which
batch they come from is decided before this module is called.

Rules that hold everywhere in this file:
* Steps that already started are fixed to their real start time.
* A constraint between two steps that both already happened is skipped:
  facts are not re-judged.
* The result is always re-checked by ``validator.validate`` (which does not
  use OR-Tools) before it is returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .types import ScheduledStep, ScheduleInput, ScheduleResult
from .validator import validate


class SchedulerBug(RuntimeError):
    """The solver produced a plan that the independent validator rejects."""


@dataclass
class _Built:
    model: cp_model.CpModel
    start: dict[str, cp_model.IntVar]
    end: dict[str, cp_model.IntVar]
    ready: dict[str, cp_model.IntVar]
    finish: cp_model.IntVar
    objectives: list[cp_model.LinearExprT] = field(default_factory=list)


def _frozen(inp: ScheduleInput, step_id: str) -> bool:
    return step_id in inp.frozen


def _precedes(inp: ScheduleInput) -> dict[str, set[str]]:
    """step -> every step that must come after it because of the chains.

    Only used to drop oven-mode choices whose answer is already forced.
    """
    after: dict[str, set[str]] = {}
    trays_of: dict[str, list] = {}
    for t in inp.trays:
        ids = [s.id for s in t.steps]
        for i, a in enumerate(ids):
            after.setdefault(a, set()).update(ids[i + 1 :])
        trays_of.setdefault(t.batch_id, []).append(ids)
    for b in inp.batches:
        ids = [s.id for s in b.steps]
        downstream = {sid for tray_ids in trays_of.get(b.id, []) for sid in tray_ids}
        for i, a in enumerate(ids):
            after.setdefault(a, set()).update(ids[i + 1 :], downstream)
    return after


def _build(
    inp: ScheduleInput,
    *,
    enforce_request: bool = True,
    day_end: int | None = None,
) -> _Built:
    m = cp_model.CpModel()
    steps = inp.all_steps()
    horizon_end = inp.day_end if day_end is None else day_end
    earliest = max(inp.day_start, inp.now)

    start: dict[str, cp_model.IntVar] = {}
    end: dict[str, cp_model.IntVar] = {}
    interval: dict[str, cp_model.IntervalVar] = {}
    big = 24 * 60 * 2

    for sid, step in steps.items():
        size = inp.size_of(sid)
        f = inp.frozen.get(sid)
        if f is not None:
            s = m.new_int_var(f.start, f.start, f"s_{sid}")
            e = m.new_int_var(f.start + size, f.start + size, f"e_{sid}")
        else:
            if earliest + size > horizon_end:
                # Cannot fit at all; keep the model valid and let it be infeasible.
                s = m.new_int_var(earliest, earliest, f"s_{sid}")
                m.add_bool_or([])  # always false -> infeasible
            else:
                s = m.new_int_var(earliest, horizon_end - size, f"s_{sid}")
            e = m.new_int_var(0, big, f"e_{sid}")
        m.add(e == s + size)
        start[sid], end[sid] = s, e
        interval[sid] = m.new_interval_var(s, size, e, f"i_{sid}")

    def chain(ids: list[str], waits: list[int | None]) -> None:
        for (a, b), w in zip(zip(ids, ids[1:]), waits):
            if _frozen(inp, a) and _frozen(inp, b):
                continue
            m.add(start[b] >= end[a])
            if w is not None:
                m.add(start[b] - end[a] <= w)

    for b in inp.batches:
        chain([s.id for s in b.steps], list(b.waits))
    trays_of: dict[str, list] = {b.id: [] for b in inp.batches}
    for t in inp.trays:
        chain([s.id for s in t.steps], list(t.waits))
        trays_of[t.batch_id].append(t)

    # Batch -> its trays: shaping starts after the batch is ready and all trays
    # of the batch are shaped back to back (plus the configured slack).
    for b in inp.batches:
        last = b.steps[-1].id
        trays = trays_of[b.id]
        window = sum(inp.size_of(t.steps[0].id) for t in trays) + b.shape_slack
        for t in trays:
            first = t.steps[0].id
            if _frozen(inp, first) and _frozen(inp, last):
                continue
            m.add(start[first] >= end[last])
            if not _frozen(inp, first):
                m.add(end[first] - end[last] <= window)

    tray_by_id = {t.id: t for t in inp.trays}
    ready: dict[str, cp_model.IntVar] = {}
    for o in inp.orders:
        pk = o.package.id
        for tid in o.tray_ids:
            last = tray_by_id[tid].steps[-1].id
            if not (_frozen(inp, last) and _frozen(inp, pk)):
                m.add(start[pk] >= end[last])
        ready[o.id] = end[pk]
        if o.promise_ready_by is not None and not _frozen(inp, pk):
            m.add(end[pk] <= o.promise_ready_by)

    if inp.request is not None and enforce_request:
        m.add(ready[inp.request.order_id] <= inp.request.ready_by)

    # Resources.
    person = [interval[s] for s, st in steps.items() if st.where == "person"]
    m.add_no_overlap(person)
    proof = [s for s, st in steps.items() if st.where == "oven_proof"]
    bake = [s for s, st in steps.items() if st.where == "oven_bake"]
    if proof:
        m.add_cumulative(
            [interval[s] for s in proof], [steps[s].demand for s in proof], inp.oven.proof_slots
        )
    if bake:
        m.add_cumulative([interval[s] for s in bake], [1] * len(bake), inp.oven.bake_trays)

    # One chamber: proof and bake never overlap, and switching takes time.
    sw_pb = inp.oven.switch_proof_to_bake
    sw_bp = inp.oven.switch_bake_to_proof
    before = _precedes(inp)
    for p in proof:
        for b in bake:
            if _frozen(inp, p) and _frozen(inp, b):
                continue
            if b in before.get(p, ()):  # p always comes first (same chain)
                m.add(end[p] + sw_pb <= start[b])
                continue
            x = m.new_bool_var(f"pb_{p}_{b}")
            m.add(end[p] + sw_pb <= start[b]).only_enforce_if(x)
            m.add(end[b] + sw_bp <= start[p]).only_enforce_if(~x)

    finish = m.new_int_var(0, big, "finish")
    m.add_max_equality(finish, list(end.values()))
    return _Built(m, start, end, ready, finish)


def _objectives(inp: ScheduleInput, built: _Built) -> list:
    m = built.model
    if inp.objective == "initial":
        return [built.finish, sum(built.ready.values())]
    # minimize_delay: protect the existing plan first.
    target = inp.request.order_id if inp.request else None
    delays = []
    for oid, prev in inp.previous_ready.items():
        if oid == target or oid not in built.ready:
            continue
        d = m.new_int_var(0, 24 * 60 * 2, f"delay_{oid}")
        m.add(d >= built.ready[oid] - prev)
        delays.append(d)
    objs: list = [sum(delays)] if delays else []
    if target is not None:
        objs.append(built.ready[target])
    objs.append(built.finish)
    return objs


def _solver(time_limit_s: float, seed: int) -> cp_model.CpSolver:
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit_s
    # One worker: same input -> same plan, every time. Multi-worker search
    # breaks ties differently between runs, which makes diffs and the demo
    # flicker. Measured faster at this problem size too (see DECISIONS.md).
    s.parameters.num_workers = 1
    s.parameters.random_seed = seed
    return s


def _lexicographic(inp: ScheduleInput, built: _Built, objs: list):
    """Optimise objectives in order, fixing each before the next.

    Returns (first_status, solution dict or None, all_optimal).
    """
    m = built.model
    per_phase = max(0.01, inp.time_limit_s / max(1, len(objs)))
    solution = None
    all_optimal = True
    first_status = None
    for k, obj in enumerate(objs):
        m.minimize(obj)
        solver = _solver(per_phase, inp.seed)
        st = solver.solve(m)
        if k == 0:
            first_status = st
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if k == 0:
                return first_status, None, False
            all_optimal = False
            break  # keep the previous phase's solution
        if st != cp_model.OPTIMAL:
            all_optimal = False
        solution = {sid: solver.value(v) for sid, v in built.start.items()}
        value = int(solver.objective_value)
        m.add(obj <= value)
        m.clear_hints()
        for sid, v in built.start.items():
            m.add_hint(v, solution[sid])
    return first_status, solution, all_optimal


def _to_result(inp: ScheduleInput, solution: dict[str, int], optimal: bool, status_name: str):
    steps = inp.all_steps()
    out = {
        sid: ScheduledStep(
            id=sid, where=st.where, start=solution[sid], end=solution[sid] + inp.size_of(sid)
        )
        for sid, st in steps.items()
    }
    ready = {o.id: out[o.package.id].end for o in inp.orders}
    return ScheduleResult(
        status="feasible",
        steps=out,
        order_ready=ready,
        day_finish=max(s.end for s in out.values()),
        solver_status=status_name,
        optimal=optimal,
    )


def solve(inp: ScheduleInput) -> ScheduleResult:
    built = _build(inp)
    objs = _objectives(inp, built)
    first, solution, optimal = _lexicographic(inp, built, objs)
    name = cp_model.CpSolver().status_name(first) if first is not None else "NONE"
    if solution is None:
        if first == cp_model.INFEASIBLE:
            return ScheduleResult(status="infeasible", solver_status=name)
        if first == cp_model.MODEL_INVALID:
            raise SchedulerBug("CP-SAT says the model is invalid")
        return ScheduleResult(status="not_found", solver_status=name)
    result = _to_result(inp, solution, optimal, name)
    violations = validate(inp, result)
    if violations:
        raise SchedulerBug("validator rejected solver output: " + "; ".join(violations))
    return result


@dataclass
class Earliest:
    status: str  # "ok" | "exceeds_day_end" | "impossible" | "not_found"
    ready: int | None
    # True only if CP-SAT proved no earlier time exists. If False, ``ready``
    # is a time that works, not necessarily the earliest one.
    proven: bool = False


LATEST_POSSIBLE = 23 * 60 + 59


def earliest_ready(inp: ScheduleInput, order_id: str) -> Earliest:
    """Earliest time ``order_id`` can be ready while keeping every promise.

    Used when a request is infeasible. All hard constraints (promises,
    frozen facts) stay in place, so the answer never sacrifices anyone else.
    """
    for day_end, label in ((inp.day_end, "ok"), (LATEST_POSSIBLE, "exceeds_day_end")):
        built = _build(inp, enforce_request=False, day_end=day_end)
        built.model.minimize(built.ready[order_id])
        solver = _solver(inp.time_limit_s, inp.seed)
        st = solver.solve(built.model)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return Earliest(
                label, int(solver.value(built.ready[order_id])), proven=st == cp_model.OPTIMAL
            )
        if st != cp_model.INFEASIBLE:
            return Earliest("not_found", None)
    return Earliest("impossible", None)
