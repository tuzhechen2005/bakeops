"""Scheduler tests. All numbers are test values from kitchen.example.yaml."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bakeops.scheduler import earliest_ready, solve, validate
from bakeops.scheduler.builder import (
    BatchLayout,
    ConfigError,
    OrderLayout,
    TrayLayout,
    build_input,
    load_config,
    min_to_hhmm,
)
from bakeops.scheduler.types import Frozen, ScheduledStep

CFG = load_config(Path(__file__).resolve().parents[2] / "config" / "kitchen.example.yaml")


def two_batches():
    batches = [
        BatchLayout(id="B1", trays=[TrayLayout(id="T1", product_id="red_bean"), TrayLayout(id="T2", product_id="cheese")]),
        BatchLayout(id="B2", trays=[TrayLayout(id="T3", product_id="pork_floss"), TrayLayout(id="T4", product_id="milk_roll")]),
    ]
    orders = [
        OrderLayout(id="zhang", tray_ids=["T1", "T2"]),
        OrderLayout(id="wang", tray_ids=["T3"]),
        OrderLayout(id="zhao", tray_ids=["T4"]),
    ]
    return batches, orders


def freeze_until(plan, now: int) -> dict[str, Frozen]:
    return {
        sid: Frozen(start=s.start, end=s.end if s.end <= now else None)
        for sid, s in plan.steps.items()
        if s.start <= now
    }


# --- basic correctness ------------------------------------------------------


def test_single_tray_equals_hand_computed_chain():
    batches = [BatchLayout(id="B1", trays=[TrayLayout(id="T1", product_id="red_bean")])]
    orders = [OrderLayout(id="o1", tray_ids=["T1"])]
    res = solve(build_input(CFG, batches, orders, now="06:00"))
    # mix 15 + bulk 50 + divide (10+2) + rest 20 + shape 12 + proof 40
    # + pre-heat 15 (one chamber) + bake 15 + cool 20 + package 3 = 202
    assert res.status == "feasible"
    assert res.order_ready["o1"] == 6 * 60 + 202


def test_morning_plan_is_valid_and_deterministic():
    batches, orders = two_batches()
    a = solve(build_input(CFG, batches, orders, now="06:00"))
    b = solve(build_input(CFG, batches, orders, now="06:00"))
    assert a.status == "feasible" and a.optimal
    assert a.steps == b.steps


def test_oven_never_in_two_modes():
    batches, orders = two_batches()
    inp = build_input(CFG, batches, orders, now="06:00")
    res = solve(inp)
    proof = [s for s in res.steps.values() if s.where == "oven_proof"]
    bake = [s for s in res.steps.values() if s.where == "oven_bake"]
    for p in proof:
        for b in bake:
            assert p.end + inp.oven.switch_proof_to_bake <= b.start or b.end + inp.oven.switch_bake_to_proof <= p.start


# --- the three outcomes -------------------------------------------------------


def test_too_early_request_is_infeasible_and_earliest_is_real():
    batches, orders = two_batches()
    plan = solve(build_input(CFG, batches, orders, now="06:00"))
    frozen = freeze_until(plan, 8 * 60)
    target = min(plan.order_ready.values()) - 15
    req = build_input(
        CFG, batches, orders, now="08:00", frozen=frozen, previous_ready=plan.order_ready,
        request=("wang", min_to_hhmm(target)), objective="minimize_delay",
    )
    assert solve(req).status == "infeasible"
    e = earliest_ready(req, "wang")
    assert e.status == "ok" and e.proven
    # The earliest time it reports must actually be achievable.
    again = req.model_copy(update={"request": req.request.model_copy(update={"ready_by": e.ready})})
    assert solve(again).status == "feasible"


def test_not_found_is_not_reported_as_infeasible():
    batches = [
        BatchLayout(id=f"B{b}", trays=[TrayLayout(id=f"T{b}{i}", product_id="red_bean") for i in range(3)])
        for b in range(1, 5)
    ]
    orders = [OrderLayout(id=f"o{b}{i}", tray_ids=[f"T{b}{i}"]) for b in range(1, 5) for i in range(3)]
    # 4 x 3 trays does not fit before 20:00 (proven in ~4 s with a full budget).
    # With 0.01 s the solver cannot prove that, so it must say "not found",
    # never "impossible".
    res = solve(build_input(CFG, batches, orders, now="06:00", time_limit_s=0.01))
    assert res.status == "not_found"
    assert res.solver_status == "UNKNOWN"


def test_four_trays_per_batch_cannot_fit_one_chamber():
    """Documents a real finding: with one chamber and these test values,
    four trays from one batch over-proof while waiting for the oven."""
    batches = [BatchLayout(id="B1", trays=[TrayLayout(id=f"T{i}", product_id="cheese") for i in range(4)])]
    orders = [OrderLayout(id="o", tray_ids=[f"T{i}" for i in range(4)])]
    assert solve(build_input(CFG, batches, orders, now="06:00")).status == "infeasible"


# --- promises and facts ------------------------------------------------------


def test_swap_allowed_without_promises_refused_with_promise():
    batches, orders = two_batches()
    plan = solve(build_input(CFG, batches, orders, now="06:00"))
    kw = dict(now="06:00", previous_ready=plan.order_ready, request=("zhang", "10:30"), objective="minimize_delay")

    free = solve(build_input(CFG, batches, orders, **kw))
    assert free.status == "feasible"
    assert free.order_ready["zhang"] <= 10 * 60 + 30

    promised = [
        o.model_copy(update={"promise_ready_by": min_to_hhmm(plan.order_ready["wang"])}) if o.id == "wang" else o
        for o in orders
    ]
    assert solve(build_input(CFG, batches, promised, **kw)).status == "infeasible"


def test_frozen_steps_do_not_move():
    batches, orders = two_batches()
    plan = solve(build_input(CFG, batches, orders, now="06:00"))
    now = 8 * 60
    frozen = freeze_until(plan, now)
    res = solve(
        build_input(CFG, batches, orders, now="08:00", frozen=frozen, previous_ready=plan.order_ready, objective="minimize_delay")
    )
    assert res.status == "feasible"
    for sid, f in frozen.items():
        assert res.steps[sid].start == f.start
    for sid, s in res.steps.items():
        if sid not in frozen:
            assert s.start >= now


# --- validator catches bad plans ----------------------------------------------


@pytest.fixture
def good():
    batches, orders = two_batches()
    inp = build_input(CFG, batches, orders, now="06:00")
    return inp, solve(inp)


def _shift(res, sid, delta):
    bad = copy.deepcopy(res)
    s = bad.steps[sid]
    bad.steps[sid] = ScheduledStep(id=s.id, where=s.where, start=s.start + delta, end=s.end + delta)
    return bad


def test_validator_accepts_solver_output(good):
    inp, res = good
    assert validate(inp, res) == []


def test_validator_catches_person_overlap(good):
    inp, res = good
    person = sorted((s for s in res.steps.values() if s.where == "person"), key=lambda s: s.start)
    a, b = person[0], person[1]
    bad = _shift(res, b.id, a.start - b.start)
    assert any("person busy twice" in x for x in validate(inp, bad))


def test_validator_catches_oven_mode_clash(good):
    inp, res = good
    bake = next(s for s in res.steps.values() if s.where == "oven_bake")
    proof = next(s for s in res.steps.values() if s.where == "oven_proof" and s.end <= bake.start)
    bad = _shift(res, bake.id, proof.end - bake.start)  # bake right after proof, no pre-heat
    assert any("oven:" in x for x in validate(inp, bad))


def test_validator_catches_moved_fact(good):
    inp, res = good
    first = min(res.steps.values(), key=lambda s: s.start)
    inp2 = inp.model_copy(update={"frozen": {first.id: Frozen(start=first.start)}})
    bad = _shift(res, first.id, 5)
    assert any("already happened" in x for x in validate(inp2, bad))


def test_validator_catches_broken_promise(good):
    inp, res = good
    o = inp.orders[0]
    inp2 = inp.model_copy(update={"orders": [o.model_copy(update={"promise_ready_by": res.order_ready[o.id] - 1})] + inp.orders[1:]})
    assert any("breaks promise" in x for x in validate(inp2, res))


def test_validator_rejects_stale_plan_at_approval_time(good):
    inp, res = good
    later = min(s.start for s in res.steps.values()) + 10
    assert any("before" in x for x in validate(inp, res, now=later))


# --- config ------------------------------------------------------------------


def test_config_rejects_impossible_wait_after_proof():
    cfg = copy.deepcopy(CFG)
    for s in cfg["tray_steps"]:
        if s["id"] == "proof":
            s["max_wait_after"] = {"value": 0, "confirmed": False}
    batches, orders = two_batches()
    with pytest.raises(ConfigError):
        build_input(cfg, batches, orders, now="06:00")


# --- property test -----------------------------------------------------------

products = st.sampled_from(["red_bean", "cheese", "pork_floss", "milk_roll"])


@st.composite
def small_day(draw):
    n_batches = draw(st.integers(1, 2))
    batches, tray_ids = [], []
    for b in range(n_batches):
        n = draw(st.integers(1, 3))
        trays = [TrayLayout(id=f"T{b}{i}", product_id=draw(products)) for i in range(n)]
        tray_ids += [t.id for t in trays]
        batches.append(BatchLayout(id=f"B{b}", trays=trays))
    orders = []
    for i, tid in enumerate(tray_ids):
        extra = draw(st.sampled_from([[]] + [[x] for x in tray_ids if x != tid]))
        orders.append(OrderLayout(id=f"o{i}", tray_ids=[tid, *extra]))
    return batches, orders


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(small_day())
def test_every_feasible_plan_passes_validator(day):
    batches, orders = day
    inp = build_input(CFG, batches, orders, now="06:00")
    res = solve(inp)  # solve() raises SchedulerBug if the validator disagrees
    if res.status == "feasible":
        assert validate(inp, res) == []
