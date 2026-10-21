"""Python checks that run after the model. The model never has the last word.

Each check turns the model's output into a ``Checked*`` result with a list
of problems. Rows or fields with problems are shown in red and are never
used until the baker fixes or confirms them.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from .qtyparse import parse_qty
from .schemas import MessageInterpretation, ParseOrdersOutput
from .timeparse import parse_time


class CatalogItem(BaseModel):
    id: str
    name_zh: str
    aliases: list[str] = []


class CustomerRef(BaseModel):
    id: str
    display_name: str
    aliases: list[str] = []


class OrderRef(BaseModel):
    id: str
    summary: str  # e.g. "红豆包×4, 奶酪包×4"
    ready_at: str | None = None
    promise: str | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _in_text(snippet: str, text: str) -> bool:
    return bool(snippet) and _norm(snippet) in _norm(text)


# --- Task A ------------------------------------------------------------------


class CheckedItem(BaseModel):
    product_id: str | None
    qty: int | None
    raw_product_text: str
    qty_raw: str
    problems: list[str]


class CheckedOrder(BaseModel):
    customer_id: str | None
    raw_customer_text: str
    items: list[CheckedItem]
    evidence: str
    problems: list[str]


class CheckedOrders(BaseModel):
    orders: list[CheckedOrder]
    unparsed: list[str]
    possibly_missed: list[str]


def check_orders(
    out: ParseOrdersOutput, text: str, catalog: list[CatalogItem], customers: list[CustomerRef]
) -> CheckedOrders:
    product_ids = {p.id for p in catalog}
    customer_ids = {c.id for c in customers}
    checked: list[CheckedOrder] = []
    for o in out.orders:
        problems: list[str] = []
        if not _in_text(o.evidence, text):
            problems.append("evidence is not in the original text")
        if not _in_text(o.raw_customer_text, text):
            problems.append("customer name is not in the original text")
        cid = o.customer_id if o.customer_id in customer_ids else None
        if o.customer_id and cid is None:
            problems.append(f"unknown customer id {o.customer_id!r}")
        items: list[CheckedItem] = []
        for it in o.items:
            ip: list[str] = []
            pid = it.product_id if it.product_id in product_ids else None
            if pid is None:
                ip.append(f"'{it.raw_product_text}' is not in the product list")
            py_qty = parse_qty(it.qty_raw)
            if py_qty is None:
                ip.append(f"cannot read quantity '{it.qty_raw}'")
            elif it.qty is not None and it.qty != py_qty:
                ip.append(f"model says {it.qty}, text says '{it.qty_raw}'")
            if not _in_text(it.raw_product_text, o.evidence):
                ip.append("product text is not in this order's evidence")
            items.append(
                CheckedItem(
                    product_id=pid, qty=py_qty, raw_product_text=it.raw_product_text,
                    qty_raw=it.qty_raw, problems=ip,
                )
            )
        if not items:
            problems.append("no items")
        checked.append(
            CheckedOrder(
                customer_id=cid, raw_customer_text=o.raw_customer_text, items=items,
                evidence=o.evidence, problems=problems,
            )
        )

    # Anything in the text that no evidence and no "unparsed" covers.
    rest = _norm(text)
    for snippet in [o.evidence for o in out.orders] + out.unparsed:
        rest = rest.replace(_norm(snippet), " ", 1)
    leftovers = [x for x in re.split(r"[\s，,。；;：:]+", rest) if len(x) >= 2 and not re.fullmatch(r"周.|星期.", x)]
    return CheckedOrders(
        orders=checked,
        unparsed=[u for u in out.unparsed if _in_text(u, text)],
        possibly_missed=leftovers,
    )


# --- Task B ------------------------------------------------------------------


class CheckedMessage(BaseModel):
    intents: list[str]
    target_order_id: str | None  # None until the baker confirms when >1 order
    must_pick_order: bool
    model_target: str | None  # what the model picked (shown as a highlight only)
    candidates: list[str]
    requested_time_min: int | None
    time_status: str
    model_time_min: int | None
    add_items: list[CheckedItem]
    cancel_items: list[str]
    needs_clarification: bool
    questions: list[str]  # must be answered before anything happens
    notes: list[str]  # shown to the baker, no answer needed
    problems: list[str]


def check_message(
    out: MessageInterpretation,
    text: str,
    customer_orders: list[OrderRef],
    catalog: list[CatalogItem],
    item_ids: set[str],
    open_min: int,
    close_min: int,
) -> CheckedMessage:
    problems: list[str] = []
    questions: list[str] = []
    order_ids = [o.id for o in customer_orders]

    for ev in out.evidence:
        if not _in_text(ev, text):
            problems.append(f"evidence '{ev}' is not in the message")

    # Rule 1: with more than one order, the baker always picks. The model's
    # choice is only a highlight. With exactly one order, use it.
    model_target = out.target_order_id
    if model_target is not None and model_target not in order_ids:
        problems.append(f"model picked {model_target!r}, which is not this customer's order")
        model_target = None
    must_pick = len(order_ids) > 1
    needs_order = any(i in out.intents for i in ("ask_ready_time", "request_ready_by", "cancel", "change_quantity", "change_product"))
    if len(order_ids) == 1:
        target = order_ids[0]
    else:
        target = None
        if must_pick and needs_order:
            questions.append("这位顾客今天有多笔订单，这条消息说的是哪一笔？")
    if not order_ids and needs_order:
        problems.append("this customer has no order today")

    # Rule 2: Python reads the time; the model's number is only compared.
    tp = parse_time(out.requested_time_raw, open_min, close_min) if out.requested_time_raw else None
    if out.requested_time_raw and not _in_text(out.requested_time_raw, text):
        problems.append("time text is not in the message")
        tp = None
    req_min = None
    time_status = "none"
    if tp is not None:
        time_status = tp.status
        if tp.status == "ok":
            req_min = tp.minutes
        elif tp.status == "ambiguous":
            a, b = (f"{m // 60:02d}:{m % 60:02d}" for m in tp.candidates[:2])
            questions.append(f"'{out.requested_time_raw}' 是 {a} 还是 {b}？")
        elif tp.status == "out_of_hours":
            questions.append(f"'{out.requested_time_raw}' 不在营业时间内，请确认。")
        else:
            questions.append(f"看不懂时间 '{out.requested_time_raw}'。")
    if "request_ready_by" in out.intents and req_min is None and tp is None:
        questions.append("顾客想要几点？消息里没找到明确的时间。")

    add_items: list[CheckedItem] = []
    pids = {p.id for p in catalog}
    for it in out.add_items:
        ip: list[str] = []
        pid = it.product_id if it.product_id in pids else None
        if pid is None:
            ip.append(f"'{it.raw_product_text}' is not in the product list")
        q = parse_qty(it.qty_raw)
        if q is None:
            ip.append(f"cannot read quantity '{it.qty_raw}'")
        add_items.append(
            CheckedItem(product_id=pid, qty=q, raw_product_text=it.raw_product_text, qty_raw=it.qty_raw, problems=ip)
        )
    notes: list[str] = []
    if "add_items" in out.intents and order_ids:
        notes.append("预订后不能改数量，这条会作为新加的一单处理。")

    cancel = [c for c in out.cancel_items if c in item_ids]
    if len(cancel) != len(out.cancel_items):
        problems.append("some cancel items are not in this customer's orders")

    if out.needs_clarification and out.clarification_question:
        questions.append(out.clarification_question)

    return CheckedMessage(
        intents=list(out.intents),
        target_order_id=target,
        must_pick_order=must_pick and needs_order,
        model_target=model_target,
        candidates=order_ids,
        requested_time_min=req_min,
        time_status=time_status,
        model_time_min=out.requested_time_min,
        add_items=add_items,
        cancel_items=cancel,
        needs_clarification=bool(questions) or out.needs_clarification,
        questions=questions,
        notes=notes,
        problems=problems,
    )
