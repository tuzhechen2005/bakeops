"""Parse quantities written the way people text them: 4, 四, 十二, 两, 一打.

The model also returns a number, but Python re-parses the raw text and the
two must agree. Returns None when unsure; the UI then asks the baker.
"""

from __future__ import annotations

import re

_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_UNITS = "个只份枚袋盒"
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def cn_int(s: str) -> int | None:
    """Chinese or Arabic integer 0..999. None if it is not one."""
    s = s.strip().translate(_FULLWIDTH)
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if all(c in _DIGIT for c in s) and len(s) == 1:
        return _DIGIT[s]
    m = re.fullmatch(r"(?:([一二两三四五六七八九])百)?(?:([一二两三四五六七八九])?十)?([一二三四五六七八九])?", s)
    if not m or not s:
        return None
    hundreds, tens_digit, ones = m.groups()
    has_ten = "十" in s
    total = 0
    if hundreds:
        total += _DIGIT[hundreds] * 100
    if has_ten:
        total += (_DIGIT[tens_digit] if tens_digit else 1) * 10
    if ones:
        total += _DIGIT[ones]
    if total == 0:
        return None
    return total


def parse_qty(raw: str | None) -> int | None:
    if raw is None:
        return None
    s = raw.strip().translate(_FULLWIDTH).replace(" ", "")
    s = s.rstrip(_UNITS)
    special = {"一打": 12, "半打": 6, "两打": 24}
    if s in special:
        return special[s]
    n = cn_int(s)
    if n is None or n <= 0:
        return None
    return n
