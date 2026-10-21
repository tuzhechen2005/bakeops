"""Parse a clock time from how customers write it.

"16:00", "4点", "四点半", "下午四点", "4:30", "三点一刻", "中午12点".
Times without 上午/下午 keep every reading that falls inside opening
hours. Two readings left -> ambiguous -> the baker is asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .qtyparse import cn_int

AM = ("凌晨", "早上", "早晨", "上午", "早")
NOON = ("中午",)
PM = ("下午", "傍晚", "晚上", "晚")
_NUM = r"[0-9０-９零〇一二两三四五六七八九十]+"
_FULLWIDTH = str.maketrans("０１２３４５６７８９：", "0123456789:")

_COLON = re.compile(rf"(?P<q>{'|'.join(AM + NOON + PM)})?\s*(?P<h>\d{{1,2}}):(?P<m>\d{{2}})")
_DIAN = re.compile(
    rf"(?P<q>{'|'.join(AM + NOON + PM)})?\s*(?P<h>{_NUM})\s*[点點时]"
    rf"(?:\s*(?P<half>半)|\s*(?P<ke>一刻|三刻)|\s*(?P<m>{_NUM})\s*分?)?"
)

Status = Literal["ok", "ambiguous", "out_of_hours", "none"]


@dataclass
class TimeParse:
    status: Status
    minutes: int | None = None  # set when status == "ok"
    candidates: list[int] = field(default_factory=list)  # in opening hours
    all_readings: list[int] = field(default_factory=list)
    matched: str | None = None


def _readings(q: str | None, h: int, m: int) -> list[int]:
    if not (0 <= h <= 24 and 0 <= m < 60):
        return []
    if q in AM:
        hs = [h % 12] if h != 12 else [0]
    elif q in NOON:
        hs = [h if h >= 11 else h + 12]
    elif q in PM:
        hs = [h + 12 if h < 12 else h]
    else:
        hs = [h] if (h == 0 or h >= 12) else [h, h + 12]
    return [x * 60 + m for x in hs if x * 60 + m < 24 * 60]


def parse_time(text: str | None, open_min: int, close_min: int) -> TimeParse:
    if not text:
        return TimeParse("none")
    s = text.translate(_FULLWIDTH)
    readings: list[int] = []
    matched = None
    mt = _COLON.search(s)
    if mt:
        matched = mt.group(0)
        readings = _readings(mt.group("q"), int(mt.group("h")), int(mt.group("m")))
    else:
        mt = _DIAN.search(s)
        if mt:
            matched = mt.group(0)
            h = cn_int(mt.group("h"))
            if mt.group("half"):
                m = 30
            elif mt.group("ke"):
                m = 15 if mt.group("ke") == "一刻" else 45
            elif mt.group("m"):
                m = cn_int(mt.group("m")) or 0
            else:
                m = 0
            if h is not None:
                readings = _readings(mt.group("q"), h, m)
    if not readings:
        return TimeParse("none", matched=matched)
    inside = sorted({r for r in readings if open_min <= r <= close_min})
    if not inside:
        return TimeParse("out_of_hours", all_readings=readings, matched=matched)
    if len(inside) > 1:
        return TimeParse("ambiguous", candidates=inside, all_readings=readings, matched=matched)
    return TimeParse("ok", minutes=inside[0], candidates=inside, all_readings=readings, matched=matched)
