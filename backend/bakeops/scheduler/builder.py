"""Turn the kitchen config + a tray layout into a ScheduleInput.

The tray layout (which trays exist, which batch each comes from, which
orders each tray serves) is decided by the domain layer. This module only
expands steps and checks that the config can ever be scheduled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .types import Batch, Frozen, Oven, Order, Request, ScheduleInput, Step, Tray


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def _val(x: Any) -> Any:
    """Config values may be plain or {value, confirmed}."""
    return x["value"] if isinstance(x, dict) else x


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def unconfirmed(cfg: dict) -> list[str]:
    """Names of every number the baker has not confirmed."""
    out: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "value" in node and node.get("confirmed") is False:
                out.append(path)
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for item in node:
                name = item.get("id", "?") if isinstance(item, dict) else "?"
                walk(item, f"{path}[{name}]")

    walk(cfg, "")
    return out


class ConfigError(ValueError):
    pass


def check_config(cfg: dict) -> None:
    """Reject configs that can never produce a plan.

    One oven is both proofer and oven. A tray that proofs in the oven and
    then bakes in it must wait at least the pre-heat time in between. If the
    allowed wait is shorter, no plan can ever exist.
    """
    steps = cfg["tray_steps"]
    preheat = _val(cfg["oven"]["switch_proof_to_bake_min"])
    for a, b in zip(steps, steps[1:]):
        if a["where"] == "oven_proof" and b["where"] == "oven_bake":
            w = _val(a.get("max_wait_after"))
            if w is not None and w < preheat:
                raise ConfigError(
                    f"'{a['id']}' -> '{b['id']}': max wait {w} min is shorter than "
                    f"oven pre-heat {preheat} min. With one oven this can never be scheduled."
                )


class TrayLayout(BaseModel):
    id: str
    product_id: str


class BatchLayout(BaseModel):
    id: str
    trays: list[TrayLayout]


class OrderLayout(BaseModel):
    id: str
    tray_ids: list[str]
    promise_ready_by: str | None = None


def build_input(
    cfg: dict,
    batches: list[BatchLayout],
    orders: list[OrderLayout],
    *,
    now: str,
    frozen: dict[str, Frozen] | None = None,
    previous_ready: dict[str, int] | None = None,
    request: tuple[str, str] | None = None,
    objective: str = "initial",
    time_limit_s: float = 5.0,
) -> ScheduleInput:
    check_config(cfg)
    oven = cfg["oven"]
    dough = cfg["dough"]
    pack_min = cfg["packaging"]["per_order_min"]

    out_batches: list[Batch] = []
    out_trays: list[Tray] = []
    for b in batches:
        n = len(b.trays)
        steps, waits = [], []
        for i, sd in enumerate(dough["steps"]):
            dur = sd["duration_min"] + sd.get("per_tray_extra_min", 0) * n
            steps.append(
                Step(id=f"{b.id}.{sd['id']}", where=sd["where"], duration=dur, demand=sd.get("slots", 1))
            )
            if i < len(dough["steps"]) - 1:
                waits.append(_val(sd.get("max_wait_after", 0)))
        out_batches.append(
            Batch(id=b.id, steps=steps, waits=waits, shape_slack=_val(dough.get("shape_slack_min", 0)))
        )
        for t in b.trays:
            tsteps, twaits = [], []
            for i, sd in enumerate(cfg["tray_steps"]):
                tsteps.append(
                    Step(id=f"{t.id}.{sd['id']}", where=sd["where"], duration=sd["duration_min"], demand=sd.get("slots", 1))
                )
                if i < len(cfg["tray_steps"]) - 1:
                    twaits.append(_val(sd.get("max_wait_after")))
            out_trays.append(Tray(id=t.id, batch_id=b.id, steps=tsteps, waits=twaits))

    out_orders = [
        Order(
            id=o.id,
            tray_ids=o.tray_ids,
            package=Step(id=f"{o.id}.package", where="person", duration=pack_min),
            promise_ready_by=hhmm_to_min(o.promise_ready_by) if o.promise_ready_by else None,
        )
        for o in orders
    ]
    return ScheduleInput(
        day_start=hhmm_to_min(cfg["day"]["earliest_start"]),
        day_end=hhmm_to_min(cfg["day"]["latest_end"]),
        now=hhmm_to_min(now),
        oven=Oven(
            proof_slots=oven["proof_slots"],
            bake_trays=oven["bake_trays"],
            switch_bake_to_proof=_val(oven["switch_bake_to_proof_min"]),
            switch_proof_to_bake=_val(oven["switch_proof_to_bake_min"]),
        ),
        batches=out_batches,
        trays=out_trays,
        orders=out_orders,
        frozen=frozen or {},
        previous_ready=previous_ready or {},
        request=Request(order_id=request[0], ready_by=hhmm_to_min(request[1])) if request else None,
        objective=objective,
        time_limit_s=time_limit_s,
    )
