"""Local model calls through Ollama, with schema-constrained JSON output.

Only this file talks to the model. Everything it returns is passed through
``checks.py`` before anything else sees it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .schemas import MessageInterpretation, ParseOrdersOutput

PROMPTS = Path(__file__).parent / "prompts"
T = TypeVar("T", bound=BaseModel)


class ModelFailed(RuntimeError):
    """The model gave no valid answer after one retry. The UI shows the
    original text and asks the baker to handle it by hand."""


@dataclass
class Call:
    output: BaseModel
    raw: str
    attempts: int
    seconds: float


class Interpreter(Protocol):
    def parse_orders(self, payload: dict) -> Call: ...
    def interpret_message(self, payload: dict) -> Call: ...


class OllamaInterpreter:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout_s: float = 60.0,
        think: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.think = think
        self.client = httpx.Client(timeout=timeout_s, transport=transport)

    def _ask(self, prompt_file: str, payload: dict, schema: type[T]) -> Call:
        system = (PROMPTS / prompt_file).read_text(encoding="utf-8")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        body: dict = {
            "model": self.model,
            "messages": messages,
            "format": schema.model_json_schema(),
            "stream": False,
            "options": {"temperature": 0, "seed": 7},
        }
        if self.think is not None:
            body["think"] = self.think
        t0 = time.perf_counter()
        last_error = ""
        raw = ""
        for attempt in (1, 2):
            try:
                r = self.client.post(f"{self.host}/api/chat", json=body)
                r.raise_for_status()
                raw = r.json()["message"]["content"]
                out = schema.model_validate_json(raw)
                return Call(out, raw, attempt, time.perf_counter() - t0)
            except (ValidationError, json.JSONDecodeError, KeyError) as e:
                last_error = str(e)[:500]
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"上面的输出不符合 schema：{last_error}\n请只输出符合 schema 的 JSON。"},
                ]
                body["messages"] = messages
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
                break
        raise ModelFailed(last_error)

    def parse_orders(self, payload: dict) -> Call:
        return self._ask("parse_orders.md", payload, ParseOrdersOutput)

    def interpret_message(self, payload: dict) -> Call:
        return self._ask("interpret_message.md", payload, MessageInterpretation)
