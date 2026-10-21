"""Input/output types for the scheduler.

All times are integer minutes since 00:00 of the planning day.
These types are deliberately low level: the domain layer (dough batches,
trays, orders) is translated into these before solving.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Where = Literal["person", "oven_proof", "oven_bake", "room"]


class Step(BaseModel):
    id: str
    where: Where
    duration: int = Field(gt=0)
    # Oven proof slots this step occupies (e.g. a dough tub may take 2).
    demand: int = Field(default=1, ge=1)


class Batch(BaseModel):
    """One batch of dough: mix -> bulk -> divide -> rest (configurable)."""

    id: str
    steps: list[Step]
    # waits[i] = max minutes between end(steps[i]) and start(steps[i+1]).
    waits: list[int]
    # Extra minutes allowed, on top of back-to-back shaping, between the end
    # of this batch's last step and the end of shaping its last tray.
    shape_slack: int = 0

    @model_validator(mode="after")
    def _check(self) -> "Batch":
        if len(self.waits) != len(self.steps) - 1:
            raise ValueError(f"batch {self.id}: need {len(self.steps) - 1} waits")
        return self


class Tray(BaseModel):
    """One tray: shape -> proof -> bake -> cool (configurable)."""

    id: str
    batch_id: str
    steps: list[Step]
    waits: list[int | None]  # None = unlimited wait

    @model_validator(mode="after")
    def _check(self) -> "Tray":
        if len(self.waits) != len(self.steps) - 1:
            raise ValueError(f"tray {self.id}: need {len(self.steps) - 1} waits")
        if self.steps[0].where != "person":
            raise ValueError(f"tray {self.id}: first step must be done by the person")
        return self


class Order(BaseModel):
    id: str
    tray_ids: list[str]
    package: Step  # done by the person after all its trays have cooled
    promise_ready_by: int | None = None


class Oven(BaseModel):
    proof_slots: int = Field(ge=1)
    bake_trays: int = Field(ge=1)
    switch_bake_to_proof: int = Field(ge=0)  # cool-down
    switch_proof_to_bake: int = Field(ge=0)  # pre-heat


class Frozen(BaseModel):
    """A step that already started (and maybe finished). Facts, not plans."""

    start: int
    end: int | None = None


class Request(BaseModel):
    order_id: str
    ready_by: int


Objective = Literal["initial", "minimize_delay"]


class ScheduleInput(BaseModel):
    day_start: int
    day_end: int
    now: int
    oven: Oven
    batches: list[Batch]
    trays: list[Tray]
    orders: list[Order]
    frozen: dict[str, Frozen] = {}
    previous_ready: dict[str, int] = {}
    request: Request | None = None
    objective: Objective = "initial"
    time_limit_s: float = 5.0
    seed: int = 7

    def all_steps(self) -> dict[str, Step]:
        out: dict[str, Step] = {}
        for b in self.batches:
            for s in b.steps:
                out[s.id] = s
        for t in self.trays:
            for s in t.steps:
                out[s.id] = s
        for o in self.orders:
            out[o.package.id] = o.package
        return out

    def size_of(self, step_id: str) -> int:
        """Planned length, or the real length if the step already finished."""
        f = self.frozen.get(step_id)
        if f is not None and f.end is not None:
            return f.end - f.start
        return self.all_steps()[step_id].duration


Status = Literal["feasible", "infeasible", "not_found"]


class ScheduledStep(BaseModel):
    id: str
    where: Where
    start: int
    end: int


class ScheduleResult(BaseModel):
    status: Status
    steps: dict[str, ScheduledStep] = {}
    order_ready: dict[str, int] = {}
    day_finish: int | None = None
    solver_status: str = ""
    optimal: bool = False
