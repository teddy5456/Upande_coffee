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

    # No active season -> ISO week, so a save is never blocked.
    _frappe.db.get_value = lambda *a, **k: None
    assert _harvest_week("2026-08-03") == datetime.date(2026, 8, 3).isocalendar()[1]

    print("ok")


if __name__ == "__main__":
    demo()
