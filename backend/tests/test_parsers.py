import pytest

from bakeops.llm.qtyparse import parse_qty
from bakeops.llm.timeparse import parse_time

OPEN, CLOSE = 6 * 60, 20 * 60


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4", 4), ("4个", 4), ("四个", 4), ("四", 4), ("两个", 2), ("俩", 2),
        ("十", 10), ("十二个", 12), ("二十", 20), ("二十四个", 24), ("一打", 12),
        ("半打", 6), ("４个", 4), ("一百", 100),
        ("", None), ("几个", None), ("零", None), ("abc", None), (None, None),
    ],
)
def test_parse_qty(raw, expected):
    assert parse_qty(raw) == expected


def hm(h, m=0):
    return h * 60 + m


@pytest.mark.parametrize(
    "text,status,minutes",
    [
        ("16:00", "ok", hm(16)),
        ("16：00前要", "ok", hm(16)),
        ("下午四点", "ok", hm(16)),
        ("四点半", "ok", hm(16, 30)),  # 04:30 is before opening, so only 16:30 is left
        ("4点", "ok", hm(16)),
        ("三点一刻能拿吗", "ok", hm(15, 15)),
        ("中午12点", "ok", hm(12)),
        ("中午一点", "ok", hm(13)),
        ("晚上7点", "ok", hm(19)),
        ("上午十点", "ok", hm(10)),
        ("十点", "ok", hm(10)),  # 22:00 is after closing, so only 10:00 is left
        ("8点", "ambiguous", None),  # 08:00 and 20:00 are both inside 06:00-20:00
        ("晚上十点", "out_of_hours", None),
        ("等会儿", "none", None),
        ("4点15分", "ok", hm(16, 15)),
    ],
)
def test_parse_time(text, status, minutes):
    r = parse_time(text, OPEN, CLOSE)
    assert r.status == status
    if minutes is not None:
        assert r.minutes == minutes


def test_ambiguous_lists_both_readings():
    r = parse_time("8点", OPEN, CLOSE)
    assert r.candidates == [hm(8), hm(20)]
