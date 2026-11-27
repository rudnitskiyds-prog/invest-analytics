"""Сборщик: шаг screener_bonds (scripts/collect_data.py) и логотипы T-Invest (core/data/tinvest.py: _brand). Без сети."""
import copy
import datetime as dt
import json

import pandas as pd
import pytest

from core.data import iss, tinvest
from scripts import collect_data as cd
from tests.test_currency_bonds import fake_iss_get_json, _no_network
from tests.test_tinvest import FakeSession, fixture, make_client, ok

LOW = {"min_rows": {"TQOB": 0, "TQCB": 0}}


# ------------------------------------------------------------------ _brand
def test_brand_camel_and_snake_case():
    assert tinvest._brand({"brand": {"logoName": "sber.png", "logoBaseColor": "#309C0B"}}) == \
        {"logo": "sber.png", "logo_color": "#309C0B"}
    assert tinvest._brand({"brand": {"logo_name": "gazp.png", "logo_base_color": "#1C64F2"}}) == \
        {"logo": "gazp.png", "logo_color": "#1C64F2"}


@pytest.mark.parametrize("x", [{}, {"brand": None}, {"brand": {}}, {"brand": {"logoName": "", "logoBaseColor": None}}])
def test_brand_empty_gives_no_fields(x):
    assert tinvest._brand(x) == {}


def test_brand_only_color():
    assert tinvest._brand({"brand": {"logoBaseColor": "#000000"}}) == {"logo_color": "#000000"}


def test_shares_and_etfs_carry_brand_from_fixture():
    sh = copy.deepcopy(fixture("shares.json"))
    sh["instruments"][0]["brand"] = {"logoName": "aaaa.png", "logoBaseColor": "#112233"}
    c, _ = make_client(FakeSession(default=lambda m, b: ok(sh)))
    rows = {r["ticker"]: r for r in tinvest.shares(c)}
    first = sh["instruments"][0]["ticker"]
    assert rows[first]["logo"] == "aaaa.png" and rows[first]["logo_color"] == "#112233"
    assert all("logo" not in r and "logo_color" not in r for t, r in rows.items() if t != first)

    et = copy.deepcopy(fixture("etfs.json"))
    for x in et["instruments"]:
        x["brand"] = {"logo_name": f"{x['ticker'].lower()}.png", "logo_base_color": ""}
    c, _ = make_client(FakeSession(default=lambda m, b: ok(et)))
    for r in tinvest.etfs(c):
        assert r["logo"] == f"{r['ticker'].lower()}.png" and "logo_color" not in r


# ------------------------------------------------------------------ screener_bonds
def test_screener_bonds_on_iss_fixture(monkeypatch):
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    rows, n, extra = cd.screener_bonds(LOW)
    assert n == len(rows) == 4 and extra == {"TQOB": 1, "TQCB": 3}
    by = {r["secid"]: r for r in rows}
    assert [r["board"] for r in rows] == ["TQCB", "TQCB", "TQCB", "TQOB"]
    assert set(rows[0]) == {"secid", "name", "board", "yield", "duration_days", "coupon_pct", "coupon_freq",
                            "maturity_date", "offer_date", "listing_level", "volume_rub", "price_pct", "nkd",
                            "face_currency"}
    ofz = by["SU26238RMFS4"]
    assert ofz["yield"] == pytest.approx(0.143) and ofz["coupon_pct"] == pytest.approx(0.071)  # доли
    assert ofz["coupon_freq"] == 2 and ofz["price_pct"] == pytest.approx(60.7)
    assert ofz["maturity_date"] == "2041-05-15" and ofz["face_currency"] == "RUB"
    assert by["RU000A0JX2M2"]["face_currency"] == "USD" and by["RU000ATSTCN1"]["face_currency"] == "CNY"
    assert by["RU000A0JX2M2"]["nkd"] == pytest.approx(2299.28)   # в рублях (валюта расчётов TQCB)
    assert by["RU000ATSTCN1"]["volume_rub"] == 0                  # нулевой оборот — не повод выкидывать


def _frame(rows):
    base = {"SECID": None, "SHORTNAME": None, "SECNAME": None, "PRICE": None, "MATDATE": None, "OFFERDATE": None,
            "EFFECTIVEYIELD": None, "YIELD": None, "DURATION": None, "COUPONPERIOD": None, "COUPONPERCENT": None,
            "LISTLEVEL": None, "VALTODAY_RUR": None, "ACCRUEDINT": None, "FACE_CURRENCY": "RUB"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_screener_bonds_zero_is_null_and_filters(monkeypatch):
    future = (dt.date.today() + dt.timedelta(days=400)).isoformat()
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    frames = {
        "TQOB": _frame([
            {"SECID": "OK", "SHORTNAME": "ОК", "PRICE": 95.5, "MATDATE": future, "EFFECTIVEYIELD": 0, "YIELD": 12.5,
             "DURATION": 0, "COUPONPERIOD": 91, "COUPONPERCENT": 10, "LISTLEVEL": 1, "VALTODAY_RUR": 1e6,
             "ACCRUEDINT": 1.234},
            {"SECID": "ZERO", "SHORTNAME": "Ноль", "PRICE": 100, "MATDATE": future, "EFFECTIVEYIELD": 0, "YIELD": 0, "DURATION": 0,
             "COUPONPERIOD": 0, "OFFERDATE": "0000-00-00"},
            {"SECID": "NOPRICE", "PRICE": 0, "MATDATE": future},
            {"SECID": "NANPRICE", "PRICE": float("nan"), "MATDATE": future},
            {"SECID": "EXPIRED", "PRICE": 100, "MATDATE": past},
        ]),
        "TQCB": _frame([]),
    }
    monkeypatch.setattr(iss, "bonds", lambda board: frames[board])
    rows, n, extra = cd.screener_bonds(LOW)
    assert [r["secid"] for r in rows] == ["OK", "ZERO"] and extra == {"TQOB": 2, "TQCB": 0}
    ok_, zero = rows
    assert ok_["yield"] == pytest.approx(0.125)     # EFFECTIVEYIELD 0 -> запасной YIELD
    assert ok_["coupon_pct"] == pytest.approx(0.10) and ok_["coupon_freq"] == 4
    assert ok_["duration_days"] is None and ok_["nkd"] == 1.23 and ok_["name"] == "ОК"
    assert ok_["listing_level"] == 1 and ok_["volume_rub"] == 1_000_000
    assert zero["yield"] is None and zero["duration_days"] is None and zero["coupon_freq"] is None
    assert zero["offer_date"] is None and zero["name"] == "Ноль" and zero["listing_level"] is None
    json.dumps(rows, allow_nan=False)   # в файле нет NaN


def test_screener_bonds_sanity_thresholds(monkeypatch):
    assert cd.SOURCES["screener_bonds"]["min_rows"] == {"TQOB": 20, "TQCB": 300}
    assert cd.SOURCES["screener_bonds"].get("default") is False
    future = (dt.date.today() + dt.timedelta(days=400)).isoformat()
    mk = lambda k, p: _frame([{"SECID": f"{p}{i}", "PRICE": 100, "MATDATE": future} for i in range(k)])  # noqa: E731
    spec = cd.SOURCES["screener_bonds"]
    monkeypatch.setattr(iss, "bonds", lambda b: mk(20, "O") if b == "TQOB" else mk(300, "C"))
    assert cd.screener_bonds(spec)[1] == 320
    monkeypatch.setattr(iss, "bonds", lambda b: mk(19, "O") if b == "TQOB" else mk(300, "C"))
    with pytest.raises(Exception):
        cd.screener_bonds(spec)
    monkeypatch.setattr(iss, "bonds", lambda b: mk(20, "O") if b == "TQOB" else mk(299, "C"))
    with pytest.raises(Exception):
        cd.screener_bonds(spec)


def test_screener_bonds_step_isolated(tmp_path, monkeypatch):
    """Сбой шага не роняет остальные источники и не трогает прежний screener_bonds.json."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setattr(iss, "get_json", _no_network)
    idx = pd.bdate_range("2010-01-01", periods=3500)
    good = pd.Series(range(3500), index=idx, dtype=float)
    old = '{"updated": "2026-01-01", "data": [{"secid": "OLD"}]}'
    (tmp_path / "screener_bonds.json").write_text(old, encoding="utf-8")
    specs = {"a": {**cd.SOURCES["cbr_gold"], "load": lambda: good}, "screener_bonds": dict(cd.SOURCES["screener_bonds"])}
    monkeypatch.setattr(cd, "SOURCES", specs)
    assert cd.main(["--only", "screener_bonds", "a"]) == 0
    assert (tmp_path / "a.json").exists()
    assert (tmp_path / "screener_bonds.json").read_text(encoding="utf-8") == old
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["a"]["ok"] and not st["screener_bonds"]["ok"]

    # без --only шаг не запускается (default=False)
    assert cd.main([]) == 0

    # успешный сбор: атомарная запись, формат файла
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    specs["screener_bonds"]["min_rows"] = {"TQOB": 0, "TQCB": 0}
    assert cd.main(["--only", "screener_bonds"]) == 0
    f = json.loads((tmp_path / "screener_bonds.json").read_text(encoding="utf-8"))
    assert f["source"] == "ISS MOEX" and f["boards"] == ["TQOB", "TQCB"] and len(f["data"]) == 4
    first = f["updated"]
    assert cd.main(["--only", "screener_bonds"]) == 0
    assert json.loads((tmp_path / "screener_bonds.json").read_text(encoding="utf-8"))["updated"] == first
