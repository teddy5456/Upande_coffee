"""Standalone check for outturn harvest-week numbering: python3 this file.

Stubs frappe so it runs without a bench/site — the only thing under test is
_harvest_week's date arithmetic.
"""
import datetime
import sys
import types
from pathlib import Path

_season = {}

_frappe = types.ModuleType("frappe")
_frappe._ = lambda s, *a, **k: s
_frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: _season or None)
_utils = types.ModuleType("frappe.utils")
_utils.flt = float
_utils.getdate = lambda d: d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))
_utils.nowdate = lambda: datetime.date.today().isoformat()
_frappe.utils = _utils
sys.modules.setdefault("frappe", _frappe)
sys.modules.setdefault("frappe.utils", _utils)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from upande_coffee.selling_hooks import _harvest_week  # noqa: E402


def _set_season(**kw):
    _season.clear()
    _season.update(kw)
    return types.SimpleNamespace(**_season)


def demo():
    # Harvest opens Sat 2026-08-01 -> week 1 runs from Mon 2026-07-27.
    s = _set_season(week_one_start=datetime.date(2026, 8, 1), start_date=None)
    _frappe.db.get_value = lambda *a, **k: s
    assert _harvest_week("2026-08-01") == 1, _harvest_week("2026-08-01")
    assert _harvest_week("2026-08-02") == 1  # Sunday, still week 1
    assert _harvest_week("2026-08-03") == 2  # Monday rolls over
    assert _harvest_week("2026-08-09") == 2
    assert _harvest_week("2026-08-10") == 3
    assert _harvest_week("2026-07-20") == 1  # before the season never goes < 1

    # week_one_start empty -> falls back to start_date.
    s2 = types.SimpleNamespace(week_one_start=None, start_date=datetime.date(2026, 8, 3))
    _frappe.db.get_value = lambda *a, **k: s2
    assert _harvest_week("2026-08-03") == 1
    assert _harvest_week("2026-08-31") == 5

    # No active season -> the October rule, NOT the ISO week. The ISO fallback
    # is what numbered a 21 Sep 2026 outturn 39EM… when the estate was on 49.
    _frappe.db.get_value = lambda *a, **k: None
    # 1 Oct 2026 is a Thursday, so the 2026/27 season opens Mon 28 Sep 2026.
    assert _harvest_week("2026-09-28") == 1, _harvest_week("2026-09-28")
    assert _harvest_week("2026-10-01") == 1
    assert _harvest_week("2026-10-05") == 2
    # Late September 2026 is the tail of the 2025/26 season, which opened
    # Mon 29 Sep 2025 (1 Oct 2025 was a Wednesday) -> week 52, not ISO 39.
    assert _harvest_week("2026-09-21") == 52, _harvest_week("2026-09-21")

    # Kaitet's real anchor: the season was declared from Mon 20 Oct 2025, which
    # is what puts 21 Sep 2026 on week 49 — the number the estate actually uses.
    s3 = types.SimpleNamespace(week_one_start=datetime.date(2025, 10, 20), start_date=None)
    _frappe.db.get_value = lambda *a, **k: s3
    assert _harvest_week("2026-09-21") == 49, _harvest_week("2026-09-21")
    assert _harvest_week("2026-09-27") == 49  # Sunday, same week
    # ...and the anchor stops applying at the next October reset.
    assert _harvest_week("2026-09-28") == 1, _harvest_week("2026-09-28")
    assert _harvest_week("2026-10-05") == 2

    print("ok")


if __name__ == "__main__":
    demo()
