"""Эталон паритета ledger/rebalance (tests/fixtures/ledger_reference.json) совпадает с тем, что считает Python сейчас.
Если упал — правился core/portfolio/*: перегенерируйте (python -m tests.web.make_ledger_reference) и проверьте JS-порт."""
import json
import math

from tests.web import make_ledger_reference as gen


def _same(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_ledger_reference_is_up_to_date():
    stored = json.loads(gen.OUT.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(gen.build(), ensure_ascii=False, allow_nan=False))
    for k in ("_note", "pandas", "numpy"):      # версии библиотек в файле — справочная информация
        stored.pop(k), fresh.pop(k)
    _same(fresh, stored)
