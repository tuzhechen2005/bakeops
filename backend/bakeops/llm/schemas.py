"""What the model must return. Sent to Ollama as a JSON schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Task A: split a pasted order list ---------------------------------------


class ParsedItem(BaseModel):
    raw_product_text: str = Field(description="Product name exactly as written in the text")
    product_id: str | None = Field(description="id from the catalog, or null if no catalog product matches")
    qty: int | None = Field(description="Quantity as a number, or null if unclear")
    qty_raw: str = Field(description="Quantity exactly as written, e.g. '四个' or '4个'")


class ParsedOrder(BaseModel):
    raw_customer_text: str = Field(description="Customer name exactly as written")
    customer_id: str | None = Field(description="id of a known customer, or null if not in the list")
    items: list[ParsedItem]
    note: str | None = None
    evidence: str = Field(description="The exact part of the original text this order comes from")


class ParseOrdersOutput(BaseModel):
    orders: list[ParsedOrder]
    unparsed: list[str] = Field(description="Parts of the text you could not understand, copied exactly")


# --- Task B: understand one customer message ---------------------------------

Intent = Literal[
    "ask_ready_time",
    "request_ready_by",
    "add_items",
    "cancel",
    "change_quantity",
    "change_product",
    "other",
]


class MessageInterpretation(BaseModel):
    intents: list[Intent]
    target_order_id: str | None = Field(
        description="The one order this message is about. null if more than one order could match."
    )
    target_candidates: list[str] = Field(description="All order ids that could match")
    requested_time_raw: str | None = Field(description="The time exactly as written, e.g. '四点'")
    requested_time_min: int | None = Field(description="Minutes since midnight, or null")
    add_items: list[ParsedItem]
    cancel_items: list[str] = Field(description="order_item ids to cancel; empty = whole order")
    needs_clarification: bool
    clarification_question: str | None = Field(description="Question for the baker, in Chinese")
    evidence: list[str] = Field(description="Exact snippets of the message you relied on")
