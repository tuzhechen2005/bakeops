"""Model interface and post-model checks, with a fake Ollama (no model needed)."""

from __future__ import annotations

import json

import httpx
import pytest

from bakeops.llm.checks import CatalogItem, OrderRef, check_message, check_orders
from bakeops.llm.interpreter import ModelFailed, OllamaInterpreter
from bakeops.llm.schemas import MessageInterpretation, ParsedItem, ParsedOrder, ParseOrdersOutput

CATALOG = [
    CatalogItem(id="red_bean", name_zh="红豆包", aliases=["红豆"]),
    CatalogItem(id="cheese", name_zh="奶酪包", aliases=["奶酪"]),
]
OPEN, CLOSE = 6 * 60, 20 * 60


def fake_ollama(*contents: str, status: int = 200):
    """Returns each content in turn as the model's reply; records requests."""
    seen: list[dict] = []
    replies = list(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": replies.pop(0)}})

    return httpx.MockTransport(handler), seen


def msg(**kw) -> MessageInterpretation:
    base = dict(
        intents=["ask_ready_time"], target_order_id=None, target_candidates=[], requested_time_raw=None,
        requested_time_min=None, add_items=[], cancel_items=[], needs_clarification=False,
        clarification_question=None, evidence=[],
    )
    base.update(kw)
    return MessageInterpretation(**base)


# --- interpreter -------------------------------------------------------------


def test_sends_schema_and_temperature_zero():
    good = msg().model_dump_json()
    transport, seen = fake_ollama(good)
    call = OllamaInterpreter("gemma4:e4b", transport=transport).interpret_message({"message": "几点能拿"})
    assert call.attempts == 1
    body = seen[0]
    assert body["model"] == "gemma4:e4b"
    assert body["options"]["temperature"] == 0
    assert body["format"]["title"] == "MessageInterpretation"
    assert "think" not in body  # only sent when asked for


def test_retries_once_on_bad_json_then_succeeds():
    transport, seen = fake_ollama("not json", msg().model_dump_json())
    call = OllamaInterpreter("m", transport=transport).interpret_message({})
    assert call.attempts == 2
    assert "不符合 schema" in seen[1]["messages"][-1]["content"]


def test_gives_up_after_second_bad_answer():
    transport, _ = fake_ollama("{}", "{}")
    with pytest.raises(ModelFailed):
        OllamaInterpreter("m", transport=transport).interpret_message({})


def test_http_error_is_model_failed():
    transport, _ = fake_ollama(status=500)
    with pytest.raises(ModelFailed):
        OllamaInterpreter("m", transport=transport).parse_orders({})


# --- order checks ------------------------------------------------------------

TEXT = "周一：张三 红豆包4个 奶酪包4个 李四 奶酪包四个"


def test_good_parse_has_no_problems():
    out = ParseOrdersOutput(
        orders=[
            ParsedOrder(raw_customer_text="张三", customer_id=None, evidence="张三 红豆包4个 奶酪包4个", items=[
                ParsedItem(raw_product_text="红豆包", product_id="red_bean", qty=4, qty_raw="4个"),
                ParsedItem(raw_product_text="奶酪包", product_id="cheese", qty=4, qty_raw="4个"),
            ]),
            ParsedOrder(raw_customer_text="李四", customer_id=None, evidence="李四 奶酪包四个", items=[
                ParsedItem(raw_product_text="奶酪包", product_id="cheese", qty=4, qty_raw="四个"),
            ]),
        ],
        unparsed=[],
    )
    c = check_orders(out, TEXT, CATALOG, [])
    assert all(not o.problems and all(not i.problems for i in o.items) for o in c.orders)
    assert c.possibly_missed == []


def test_parse_problems_are_flagged_not_fixed():
    out = ParseOrdersOutput(
        orders=[
            ParsedOrder(raw_customer_text="张三", customer_id=None, evidence="张三 红豆包4个 奶酪包4个", items=[
                ParsedItem(raw_product_text="红豆包", product_id="red_bean", qty=5, qty_raw="4个"),  # wrong number
                ParsedItem(raw_product_text="奶酪包", product_id="matcha", qty=4, qty_raw="4个"),  # not in catalog
            ]),
            ParsedOrder(raw_customer_text="李四", customer_id=None, evidence="李四 奶酪包六个", items=[  # invented text
                ParsedItem(raw_product_text="奶酪包", product_id="cheese", qty=6, qty_raw="六个"),
            ]),
        ],
        unparsed=[],
    )
    c = check_orders(out, TEXT, CATALOG, [])
    z, l = c.orders
    assert "text says '4个'" in z.items[0].problems[0] and z.items[0].qty == 4
    assert z.items[1].product_id is None
    assert "evidence is not in the original text" in l.problems
    assert c.possibly_missed  # 李四 奶酪包四个 was never covered


# --- message checks ----------------------------------------------------------

ONE = [OrderRef(id="w1", summary="肉松包×8")]
TWO = [OrderRef(id="z1", summary="奶酪包×4"), OrderRef(id="z2", summary="红豆包×2")]


def run(out, text, orders, items=frozenset()):
    return check_message(out, text, orders, CATALOG, set(items), OPEN, CLOSE)


def test_single_order_is_used():
    c = run(msg(target_order_id="w1"), "今天几点能拿？", ONE)
    assert c.target_order_id == "w1" and not c.must_pick_order and not c.needs_clarification


def test_two_orders_baker_always_picks_even_if_model_is_sure():
    c = run(msg(intents=["request_ready_by"], target_order_id="z2", requested_time_raw="四点"), "红豆包那单四点能拿吗", TWO)
    assert c.target_order_id is None
    assert c.must_pick_order
    assert c.model_target == "z2"  # shown as a highlight only
    assert c.requested_time_min == 16 * 60


def test_model_picking_someone_elses_order_is_a_problem():
    c = run(msg(target_order_id="x9"), "几点能拿", ONE)
    assert c.model_target is None and c.problems


def test_ambiguous_time_asks():
    c = run(msg(intents=["request_ready_by"], target_order_id="w1", requested_time_raw="8点", requested_time_min=480), "8点能拿吗", ONE)
    assert c.requested_time_min is None and c.needs_clarification
    assert "08:00" in c.questions[0] and "20:00" in c.questions[0]


def test_python_time_wins_over_model_number():
    c = run(msg(intents=["request_ready_by"], target_order_id="w1", requested_time_raw="三点半", requested_time_min=210), "能不能早点，三点半", ONE)
    assert c.requested_time_min == 15 * 60 + 30
    assert c.model_time_min == 210


def test_request_without_time_asks():
    c = run(msg(intents=["request_ready_by"], target_order_id="w1"), "下午能拿吗", ONE)
    assert c.needs_clarification


def test_add_items_to_existing_customer_is_noted_as_new_order():
    out = msg(intents=["add_items"], add_items=[ParsedItem(raw_product_text="红豆包", product_id="red_bean", qty=2, qty_raw="两个")])
    c = run(out, "再加两个红豆包", ONE)
    assert c.add_items[0].qty == 2 and c.notes and not c.needs_clarification


def test_cancel_items_must_belong_to_customer():
    c = run(msg(intents=["cancel"], cancel_items=["w1i1", "zz"]), "不要了", ONE, items={"w1i1"})
    assert c.cancel_items == ["w1i1"] and c.problems
