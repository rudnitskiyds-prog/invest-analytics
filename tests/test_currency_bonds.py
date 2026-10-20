"""Облигации с валютным номиналом: курсы ЦБ, витрина, котировки и история в рублях (без сети).

Фикстуры: tests/fixtures/cbr_usd_dynamic.xml, cbr_cny_dynamic.xml (выгрузки XML_dynamic.asp ЦБ),
tests/fixtures/iss_bonds_currency.json (сокращённые ответы ISS по режимам TQCB / TQOD / TQOB).
Реальные значения на 13.10.2026: RU000A0JX2M2 — номинал 1000 USD, PREVPRICE 100,9965 %,
НКД 2299,28 руб. (TQCB) = 27,40 USD (TQOD); курс USD ЦБ 83,9153.
"""
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.analytics import screener
from core.data import cbr, iss, quotes

FIX = Path(__file__).resolve().parent / "fixtures"
USD = 83.9153
_RESOLVE = iss.resolve        # оригинал с lru_cache (в тестах resolve бывает подменён)
CNY = 117.4560 / 10          # Nominal = 10 в выгрузке ЦБ

BOND_COLS = ["SECID", "Название", "Валюта", "Номинал", "Цена, %", "Цена, руб.", "Купон, %", "След. купон",
             "Погашение", "Лет до погаш.", "Дюрация, лет", "Доходность, %", "Мод. дюрация", "НКД, руб.",
             "Оборот, руб.", "Листинг"]


# ------------------------------------------------------------------ подмены источников
class FakeCBR:
    """Подмена cbr._fetch: отдаёт записи фикстуры в пределах date_req1..date_req2, пишет вызовы."""
    FILES = {"R01235": "cbr_usd_dynamic.xml", "R01375": "cbr_cny_dynamic.xml"}

    def __init__(self, fail: bool = False, fail_vids=()):
        self.calls = []
        self.fail = fail
        self.fail_vids = set(fail_vids)

    def __call__(self, url, params=None, data=None, headers=None, ttl=None):
        self.calls.append((url, dict(params or {})))
        if self.fail:
            raise ConnectionError("ЦБ недоступен (тест)")
        p = params or {}
        assert url.endswith("/scripts/XML_dynamic.asp")
        if p.get("VAL_NM_RQ") in self.fail_vids:
            raise cbr.CBRUnavailable("ЦБ недоступен (тест)")
        d1 = pd.to_datetime(p["date_req1"], format="%d/%m/%Y")
        d2 = pd.to_datetime(p["date_req2"], format="%d/%m/%Y")
        vid = p["VAL_NM_RQ"]
        fn = self.FILES.get(vid)
        if fn is None:
            return f'<?xml version="1.0" encoding="windows-1251"?><ValCurs ID="{vid}" name="x"/>'
        text = (FIX / fn).read_text(encoding="utf-8")
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))
        for rec in list(root.findall("Record")):
            if not d1 <= pd.to_datetime(rec.get("Date"), format="%d.%m.%Y") <= d2:
                root.remove(rec)
        return '<?xml version="1.0" encoding="windows-1251"?>' + ET.tostring(root, encoding="unicode")


def _no_network(*a, **k):
    raise AssertionError("обращение к сети в тесте")


def _board_json(board):
    fx = json.loads((FIX / "iss_bonds_currency.json").read_text(encoding="utf-8"))
    b = fx["boards"][board]
    return {name: {"columns": fx["columns"][name], "data": b[name]} for name in fx["columns"]}


def fake_iss_get_json(path, params=None, ttl=None, retries=3):
    m = re.fullmatch(r"/engines/stock/markets/bonds/boards/(\w+)/securities", path)
    if m:
        return _board_json(m.group(1))
    raise AssertionError(f"неожиданный запрос ISS: {path}")


@pytest.fixture
def no_net(monkeypatch):
    """Любая попытка сети — ошибка теста. IP_OFFLINE снят: курсы идут через подменённый _fetch."""
    monkeypatch.delenv("IP_OFFLINE", raising=False)
    monkeypatch.setattr(cbr, "_fetch", _no_network)
    monkeypatch.setattr(iss, "get_json", _no_network)
    _RESOLVE.cache_clear()
    yield
    _RESOLVE.cache_clear()


@pytest.fixture
def fake_cbr(monkeypatch, no_net):
    f = FakeCBR()
    monkeypatch.setattr(cbr, "_fetch", f)
    return f


@pytest.fixture
def fixed_rates(monkeypatch, no_net):
    """Курсы «на сегодня» без зависимости от текущей даты: подмена currency_rate_on."""
    calls = []

    def rate_on(code, date=None):
        c = cbr.normalize_currency(code)
        calls.append(c)
        if c == "RUB":
            return 1.0
        if c == "USD":
            return USD
        if c == "CNY":
            return CNY
        raise LookupError(c)
    monkeypatch.setattr(cbr, "currency_rate_on", rate_on)
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    return calls


# ------------------------------------------------------------------ 1. normalize_currency
@pytest.mark.parametrize("code, exp", [
    ("SUR", "RUB"), ("RUR", "RUB"), ("RUB", "RUB"), ("sur", "RUB"), (" SUR ", "RUB"),
    (None, "RUB"), ("", "RUB"), (float("nan"), "RUB"), (np.nan, "RUB"), (pd.NA, "RUB"),
    ("usd", "USD"), ("CNY", "CNY"), (" eur ", "EUR"),
])
def test_normalize_currency(code, exp):
    assert cbr.normalize_currency(code) == exp


# ------------------------------------------------------------------ 2–3. курсы ЦБ
def test_currency_rate_parses_xml(fake_cbr):
    s = cbr.currency_rate("USD", "2026-10-01", "2026-10-13")
    assert list(s.index) == [pd.Timestamp(d) for d in ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-13"]]
    assert list(s.values) == pytest.approx([82.5, 83.1, 83.5, 83.9153])
    assert s.name == "USD"
    assert isinstance(s.index, pd.DatetimeIndex)
    # запрос: код ЦБ и даты в формате dd/mm/yyyy
    url, p = fake_cbr.calls[0]
    assert p["VAL_NM_RQ"] == "R01235"
    assert p["date_req1"] == "01/10/2026" and p["date_req2"] == "13/10/2026"


def test_currency_rate_divides_by_nominal(fake_cbr):
    s = cbr.currency_rate("cny", "2026-10-01", "2026-10-13")
    assert s.loc["2026-10-10"] == pytest.approx(11.7456)
    assert s.loc["2026-10-09"] == pytest.approx(11.68)


def test_currency_rate_dayfirst_not_monthfirst(monkeypatch, no_net):
    # 02.03.2026 — 2 марта, а не 3 февраля
    xml = ('<ValCurs ID="R01239" name="x"><Record Date="02.03.2026" Id="R01239"><Nominal>1</Nominal>'
           '<Value>91,2345</Value></Record></ValCurs>')
    monkeypatch.setattr(cbr, "_fetch", lambda *a, **k: xml)
    s = cbr.currency_rate("EUR", "2026-02-01", "2026-03-10")
    assert list(s.index) == [pd.Timestamp("2026-03-02")]
    assert s.iloc[0] == pytest.approx(91.2345)


def test_currency_rate_unknown_currency(no_net):
    with pytest.raises(ValueError):
        cbr.currency_rate("XXX", "2026-10-01", "2026-10-13")


def test_currency_rate_rub_without_network(no_net):
    s = cbr.currency_rate("SUR", "2026-10-05", "2026-10-11")
    assert (s == 1.0).all()
    assert list(s.index) == list(pd.bdate_range("2026-10-05", "2026-10-09"))


def test_currency_rate_long_range_split_into_chunks(fake_cbr):
    cbr.currency_rate("USD", "2014-01-01", "2026-10-13")
    ranges = [(p["date_req1"], p["date_req2"]) for _, p in fake_cbr.calls]
    assert ranges == [("01/01/2014", "31/12/2018"), ("01/01/2019", "31/12/2023"), ("01/01/2024", "13/10/2026")]


@pytest.mark.parametrize("code", ["RUB", "SUR", "RUR", None, ""])
def test_currency_rate_on_rub_is_one_without_network(no_net, code):
    assert cbr.currency_rate_on(code) == 1.0
    assert cbr.currency_rate_on(code, "2026-10-11") == 1.0


def test_currency_rate_on_weekend_takes_previous(fake_cbr):
    # воскресенье 11.10 — действует курс, установленный на субботу 10.10
    assert cbr.currency_rate_on("USD", "2026-10-11") == pytest.approx(83.5)
    # понедельник 12.10 — курса на эту дату нет, действует 10.10
    assert cbr.currency_rate_on("USD", "2026-10-12") == pytest.approx(83.5)
    # на саму дату установления
    assert cbr.currency_rate_on("USD", "2026-10-13") == pytest.approx(USD)
    # окно запроса: 14 дней назад .. дата (курс на 14.10 «из будущего» не берётся)
    _, p = fake_cbr.calls[-1]
    assert p["date_req1"] == "29/09/2026" and p["date_req2"] == "13/10/2026"


def test_currency_rate_on_timestamp_with_time(fake_cbr):
    assert cbr.currency_rate_on("USD", pd.Timestamp("2026-10-13 15:30")) == pytest.approx(USD)


def test_currency_rate_on_empty_window(fake_cbr):
    with pytest.raises(LookupError):
        cbr.currency_rate_on("USD", "2026-09-01")     # в окне 18.08–01.09 записей нет
    with pytest.raises(LookupError):
        cbr.currency_rate_on("EUR", "2026-10-13")     # ЦБ вернул пустой ValCurs


def test_rates_on(fake_cbr):
    r = cbr.rates_on(["SUR", "RUB", "usd", "CNY", "XXX", None], "2026-10-12")
    assert set(r) == {"RUB", "USD", "CNY", "XXX"}
    assert r["RUB"] == 1.0 and r["USD"] == pytest.approx(83.5) and r["CNY"] == pytest.approx(11.7456)
    assert math.isnan(r["XXX"])
    # по одному запросу на валюту; рубль — без сети
    assert sorted(p["VAL_NM_RQ"] for _, p in fake_cbr.calls) == ["R01235", "R01375"]


def test_rates_on_network_error_gives_nan(monkeypatch, no_net):
    monkeypatch.setattr(cbr, "_fetch", FakeCBR(fail=True))
    r = cbr.rates_on({"USD", "SUR"}, "2026-10-13")
    assert r["RUB"] == 1.0 and math.isnan(r["USD"])


# ------------------------------------------------------------------ iss.bonds
def test_iss_bonds_currency_columns(fixed_rates):
    df = iss.bonds("TQCB").set_index("SECID")
    assert df.loc["RU000A0JX2M2", "FACE_CURRENCY"] == "USD"
    assert df.loc["RU000A0JX2M2", "SETTLE_CURRENCY"] == "RUB"
    assert df.loc["RU000ATEST01", "FACE_CURRENCY"] == "RUB"
    assert df.loc["RU000ATSTCN1", "FACE_CURRENCY"] == "CNY"
    assert df.loc["RU000A0JX2M2", "PRICE"] == pytest.approx(100.9965)     # LAST пусто → PREVPRICE
    od = iss.bonds("TQOD").set_index("SECID")
    assert od.loc["RU000A0JX2M2", "SETTLE_CURRENCY"] == "USD"


def test_iss_bonds_without_currency_columns(monkeypatch, no_net):
    def gj(path, params=None, ttl=None, retries=3):
        d = _board_json("TQOB")
        sec = d["securities"]
        drop = [sec["columns"].index(c) for c in ("FACEUNIT", "CURRENCYID")]
        sec["columns"] = [c for i, c in enumerate(sec["columns"]) if i not in drop]
        sec["data"] = [[v for i, v in enumerate(r) if i not in drop] for r in sec["data"]]
        return d
    monkeypatch.setattr(iss, "get_json", gj)
    df = iss.bonds("TQOB")
    assert list(df["FACE_CURRENCY"]) == ["RUB"] and list(df["SETTLE_CURRENCY"]) == ["RUB"]


# ------------------------------------------------------------------ 4. витрина облигаций
def test_bonds_showcase_usd_bond(fixed_rates):
    df = screener.bonds_showcase("TQCB").set_index("SECID")
    r = df.loc["RU000A0JX2M2"]
    assert r["Валюта"] == "USD" and r["Номинал"] == 1000
    assert r["Цена, %"] == pytest.approx(100.9965)
    assert r["Цена, руб."] == pytest.approx(100.9965 / 100 * 1000 * USD)        # ≈ 84 750,51
    # НКД в TQCB публикуется в рублях (CURRENCYID = SUR) — курсом не умножается
    assert r["НКД, руб."] == pytest.approx(2299.28)
    # рублёвая бумага: LAST 99,0 → 990 руб.
    assert df.loc["RU000ATEST01", "Цена, руб."] == pytest.approx(990.0)
    assert df.loc["RU000ATEST01", "НКД, руб."] == pytest.approx(21.78)
    # юаневая: курс = Value / Nominal
    assert df.loc["RU000ATSTCN1", "Цена, руб."] == pytest.approx(99.0 / 100 * 1000 * CNY)
    assert df.loc["RU000ATSTCN1", "Валюта"] == "CNY"
    # по одному курсу на валюту
    assert sorted(fixed_rates) == ["CNY", "RUB", "USD"]


def test_bonds_showcase_settle_usd_accrued_converted(fixed_rates):
    r = screener.bonds_showcase("TQOD").set_index("SECID").loc["RU000A0JX2M2"]
    assert r["НКД, руб."] == pytest.approx(27.40 * USD)
    assert r["НКД, руб."] == pytest.approx(2299.28, abs=0.01)     # то же, что в TQCB
    assert r["Цена, руб."] == pytest.approx(100.9965 / 100 * 1000 * USD)


def test_bonds_showcase_columns_order(fixed_rates):
    assert list(screener.bonds_showcase("TQCB").columns) == BOND_COLS


def test_bonds_showcase_other_metrics(fixed_rates):
    r = screener.bonds_showcase("TQOB").set_index("SECID").loc["SU26238RMFS4"]
    assert r["Доходность, %"] == pytest.approx(14.3)            # EFFECTIVEYIELD приоритетнее YIELD
    assert r["Дюрация, лет"] == pytest.approx(3300 / 365.25)
    assert r["Мод. дюрация"] == pytest.approx(3300 / 365.25 / 1.143)
    assert r["Оборот, руб."] == 900_000_000.0


def test_bonds_showcase_rate_error_gives_nan(monkeypatch, no_net):
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    monkeypatch.setattr(cbr, "_fetch", FakeCBR(fail=True))
    df = screener.bonds_showcase("TQCB").set_index("SECID")      # без исключения
    assert math.isnan(df.loc["RU000A0JX2M2", "Цена, руб."])
    assert math.isnan(df.loc["RU000ATSTCN1", "Цена, руб."])
    # рублёвая цена и НКД в рублях не страдают
    assert df.loc["RU000ATEST01", "Цена, руб."] == pytest.approx(990.0)
    assert df.loc["RU000A0JX2M2", "НКД, руб."] == pytest.approx(2299.28)


def test_bonds_showcase_rub_board_no_cbr_calls(monkeypatch, no_net):
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)      # cbr._fetch остаётся «запрещённым»
    df = screener.bonds_showcase("TQOB")
    assert df["Цена, руб."].iloc[0] == pytest.approx(607.0)
    assert df["НКД, руб."].iloc[0] == pytest.approx(28.62)
    assert list(df["Валюта"]) == ["RUB"]


# ------------------------------------------------------------------ 5. котировки
def _resolve_map(mapping):
    def resolve(secid):
        board, market = mapping[secid]
        return {"secid": secid, "engine": "stock", "market": market, "board": board}
    return resolve


def test_board_quotes_formula(fixed_rates):
    df = quotes._board_quotes("TQCB", "bonds").set_index("SECID")
    assert df.loc["RU000A0JX2M2", "price_rub"] == pytest.approx(100.9965 * 1000 / 100 * USD + 2299.28)
    assert df.loc["RU000A0JX2M2", "currency"] == "USD"
    assert df.loc["RU000ATEST01", "price_rub"] == pytest.approx(990.0 + 21.78)
    assert df.loc["RU000ATSTCN1", "price_rub"] == pytest.approx(990.0 * CNY + 150.0)
    od = quotes._board_quotes("TQOD", "bonds").set_index("SECID")
    # расчёты в USD: и тело, и НКД умножаются на курс
    assert od.loc["RU000A0JX2M2", "price_rub"] == pytest.approx((1009.965 + 27.40) * USD)


def test_get_quotes_currency_column(monkeypatch, fixed_rates):
    monkeypatch.setattr(iss, "resolve", _resolve_map({
        "RU000A0JX2M2": ("TQCB", "bonds"), "RU000ATEST01": ("TQCB", "bonds"), "SU26238RMFS4": ("TQOB", "bonds")}))
    q = quotes.get_quotes(["RU000A0JX2M2", "ru000atest01", "SU26238RMFS4"])
    assert list(q.columns) == ["price", "name", "lot", "board", "market", "currency"]
    assert q.loc["RU000A0JX2M2", "price"] == pytest.approx(1009.965 * USD + 2299.28)
    assert q.loc["RU000A0JX2M2", "currency"] == "USD"
    assert q.loc["RU000ATEST01", "currency"] == "RUB"
    assert q.loc["SU26238RMFS4", "price"] == pytest.approx(607.0 + 28.62)
    assert q.loc["SU26238RMFS4", "lot"] == 1


def test_get_quotes_settle_usd(monkeypatch, fixed_rates):
    monkeypatch.setattr(iss, "resolve", _resolve_map({"RU000A0JX2M2": ("TQOD", "bonds")}))
    q = quotes.get_quotes(["RU000A0JX2M2"])
    assert q.loc["RU000A0JX2M2", "price"] == pytest.approx((1009.965 + 27.40) * USD)
    assert q.loc["RU000A0JX2M2", "board"] == "TQOD"


def test_get_quotes_cbr_unavailable(monkeypatch, no_net):
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    monkeypatch.setattr(cbr, "_fetch", FakeCBR(fail=True))
    monkeypatch.setattr(iss, "resolve", _resolve_map({
        "RU000A0JX2M2": ("TQCB", "bonds"), "RU000ATEST01": ("TQCB", "bonds")}))
    q = quotes.get_quotes(["RU000A0JX2M2", "RU000ATEST01"])
    assert math.isnan(q.loc["RU000A0JX2M2", "price"])
    assert q.loc["RU000ATEST01", "price"] == pytest.approx(1011.78)


def test_get_quotes_shares_currency_rub(monkeypatch, no_net):
    monkeypatch.setattr(iss, "resolve", _resolve_map({"SBER": ("TQBR", "shares")}))
    monkeypatch.setattr(iss, "shares", lambda board: pd.DataFrame(
        {"SECID": ["SBER"], "SHORTNAME": ["Сбербанк"], "PRICE": [300.0], "LOTSIZE": [10]}))
    q = quotes.get_quotes(["SBER"])
    assert q.loc["SBER", "currency"] == "RUB" and q.loc["SBER", "price"] == 300.0 and q.loc["SBER", "lot"] == 10


def test_get_quotes_empty():
    q = quotes.get_quotes([])
    assert q.empty and "currency" in q.columns


# ------------------------------------------------------------------ 6. история цен
def _history_patch(monkeypatch, infos, closes):
    monkeypatch.setattr(iss, "resolve", lambda s: infos[s])
    monkeypatch.setattr(iss, "close_series", lambda s, start, end=None: closes[s].copy())


def test_price_history_rub_usd_bond(monkeypatch, fake_cbr):
    d = pd.to_datetime(["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"])
    closes = {
        "SBER": pd.Series([300.0, 301.0, 302.0, 303.0], index=d, name="SBER"),
        # 12.10 сделок по облигации не было
        "RU000A0JX2M2": pd.Series([100.0, 101.0, 100.9965], index=d[[0, 1, 3]], name="RU000A0JX2M2"),
    }
    infos = {"SBER": {"market": "shares", "faceunit": "RUB"},
             "RU000A0JX2M2": {"market": "bonds", "facevalue": 1000, "faceunit": "USD"}}
    _history_patch(monkeypatch, infos, closes)
    df = quotes.price_history_rub(["SBER", "RU000A0JX2M2"], "2026-10-08", "2026-10-13")
    assert list(df.index) == list(d)
    assert list(df["SBER"]) == [300.0, 301.0, 302.0, 303.0]          # рублёвая бумага без изменений
    exp = [1000 * 82.5,                 # 08.10
           1010 * 83.1,                 # 09.10
           1010 * 83.5,                 # 12.10: цена ffill с 09.10, курс ffill с субботы 10.10
           1009.965 * 83.9153]          # 13.10
    assert list(df["RU000A0JX2M2"]) == pytest.approx(exp)
    # курс запрашивается с 1 января года (первая цена − 14 дн.) — для переиспользования кэша
    _, p = fake_cbr.calls[0]
    assert p["VAL_NM_RQ"] == "R01235" and p["date_req1"] == "01/01/2026"


def test_price_history_rub_weekend_index(monkeypatch, fake_cbr):
    # индекс итоговой таблицы содержит воскресенье (например, из ряда другой бумаги)
    d = pd.to_datetime(["2026-10-09", "2026-10-11", "2026-10-13"])
    closes = {"X": pd.Series([1.0, 1.0, 1.0], index=d, name="X"),
              "B": pd.Series([100.0], index=d[:1], name="B")}
    infos = {"X": {"market": "shares"}, "B": {"market": "bonds", "facevalue": 1000, "faceunit": "USD"}}
    _history_patch(monkeypatch, infos, closes)
    df = quotes.price_history_rub(["X", "B"], "2026-10-09", "2026-10-13")
    assert list(df["B"]) == pytest.approx([1000 * 83.1, 1000 * 83.5, 1000 * 83.9153])


def test_price_history_rub_rub_bond_no_cbr(monkeypatch, no_net):
    d = pd.to_datetime(["2026-10-08", "2026-10-09"])
    closes = {"SU26238RMFS4": pd.Series([60.0, 60.5], index=d, name="SU26238RMFS4")}
    infos = {"SU26238RMFS4": {"market": "bonds", "facevalue": "1000", "faceunit": "RUB"}}
    _history_patch(monkeypatch, infos, closes)
    df = quotes.price_history_rub(["SU26238RMFS4"], "2026-10-08", "2026-10-09")
    assert list(df["SU26238RMFS4"]) == pytest.approx([600.0, 605.0])


def test_price_history_rub_bond_without_faceunit(monkeypatch, no_net):
    # старый формат resolve (без ключа faceunit) — считается рублёвой, ЦБ не вызывается
    d = pd.to_datetime(["2026-10-08"])
    _history_patch(monkeypatch, {"B": {"market": "bonds", "facevalue": 1000}},
                   {"B": pd.Series([99.0], index=d, name="B")})
    assert quotes.price_history_rub(["B"], "2026-10-08")["B"].iloc[0] == pytest.approx(990.0)


# ------------------------------------------------------------------ 7. resolve: faceunit
BOARDS_COLS = ["secid", "boardid", "title", "board_group_id", "market_id", "market", "engine_id", "engine",
               "is_traded", "decimals", "history_from", "history_till", "listed_from", "listed_till",
               "is_primary", "currencyid", "unit"]


def _desc_json(secid, faceunit):
    desc = [["SECID", "", secid], ["NAME", "", "Газ Капитал ЗО27-1-Д"], ["SHORTNAME", "", "ГазКЗ-27Д"],
            ["ISIN", "", secid], ["FACEVALUE", "", "1000"], ["MATDATE", "", "2027-03-23"],
            ["TYPE", "", "exchange_bond"], ["GROUP", "", "stock_bonds"]]
    if faceunit is not None:
        desc.append(["FACEUNIT", "", faceunit])
    return {"description": {"columns": ["name", "title", "value"], "data": desc},
            "boards": {"columns": BOARDS_COLS, "data": [
                [secid, "TQCB", "Т+: Облигации", 58, 2, "bonds", 1, "stock", 1, 4, "2017-03-29", "2026-10-13",
                 "", "", 1, "RUB", "%"]]}}


@pytest.mark.parametrize("faceunit, exp", [("USD", "USD"), ("SUR", "RUB"), ("CNY", "CNY"), (None, "RUB")])
def test_resolve_faceunit(monkeypatch, no_net, faceunit, exp):
    monkeypatch.setattr(iss, "get_json", lambda path, params=None, ttl=None, retries=3:
                        _desc_json("RU000A0JX2M2", faceunit))
    iss.resolve.cache_clear()
    info = iss.resolve("RU000A0JX2M2")
    assert info["faceunit"] == exp
    assert info["market"] == "bonds" and info["board"] == "TQCB" and info["facevalue"] == "1000"


def test_resolve_faceunit_without_description(monkeypatch, no_net):
    d = _desc_json("RU000A0JX2M2", "USD")
    d["description"] = {"columns": ["name", "title", "value"], "data": []}
    monkeypatch.setattr(iss, "get_json", lambda *a, **k: d)
    iss.resolve.cache_clear()
    assert iss.resolve("RU000A0JX2M2")["faceunit"] == "RUB"


# ------------------------------------------------------------------ 8. смоук витрины
def test_showcase_page_bonds_tab_with_currency_bonds(monkeypatch, fixed_rates):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("IP_OFFLINE", "1")
    # прочие вкладки без сети: пустые таблицы
    monkeypatch.setattr(screener, "shares_showcase", lambda *a, **k: pd.DataFrame(
        {"SECID": pd.Series(dtype=str), "Название": pd.Series(dtype=str)}))
    monkeypatch.setattr(screener, "etf_showcase", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(iss, "metals", lambda *a, **k: pd.DataFrame(
        columns=["SECID", "NAME", "PRICE", "LASTTOPREVPRICE", "VALTODAY_RUR", "UPDATETIME"]))
    monkeypatch.setattr(iss, "indices", lambda *a, **k: pd.DataFrame())
    root = Path(__file__).resolve().parent.parent
    at = st_testing.AppTest.from_file(str(root / "pages" / "1_Витрина.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    at.radio[0].set_value("TQCB").run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    cur = [m for m in at.multiselect if m.label == "Валюта номинала"]
    assert cur and cur[0].options == ["RUB", "CNY", "USD"]
    # оставить только USD — таблица фильтруется без ошибок
    cur[0].set_value(["USD"]).run()
    assert not at.exception, at.exception
    tables = [d.value for d in at.dataframe if "Валюта" in d.value.columns]
    assert tables and list(tables[0]["Валюта"]) == ["USD"]
    assert tables[0]["Цена, руб."].iloc[0] == pytest.approx(100.9965 / 100 * 1000 * USD)


def test_price_history_rub_cbr_unavailable_gives_nan(monkeypatch, no_net, caplog):
    d = pd.to_datetime(["2026-10-08", "2026-10-09", "2026-10-12"])
    closes = {"SU26238RMFS4": pd.Series([60.0, 60.5, 61.0], index=d, name="SU26238RMFS4"),
              "RU000A0JX2M2": pd.Series([100.0, 101.0, 100.9965], index=d, name="RU000A0JX2M2")}
    infos = {"SU26238RMFS4": {"market": "bonds", "facevalue": 1000, "faceunit": "RUB"},
             "RU000A0JX2M2": {"market": "bonds", "facevalue": 1000, "faceunit": "USD"}}
    _history_patch(monkeypatch, infos, closes)

    def down(*a, **k):
        raise ConnectionError("ЦБ недоступен (тест)")
    monkeypatch.setattr(cbr, "currency_rate", down)
    with caplog.at_level("WARNING", logger="core.data.quotes"):
        df = quotes.price_history_rub(["SU26238RMFS4", "RU000A0JX2M2"], "2026-10-08", "2026-10-12")
    assert df["RU000A0JX2M2"].isna().all()
    assert list(df["SU26238RMFS4"]) == pytest.approx([600.0, 605.0, 610.0])
    warn = [r for r in caplog.records if r.name == "core.data.quotes" and r.levelname == "WARNING"]
    assert warn and "Нет курса ЦБ" in warn[0].getMessage() and "USD" in warn[0].getMessage()


# ------------------------------------------------------------------ core/data/currency.py
def test_currency_module_is_single_source():
    from core.data import currency
    assert cbr.normalize_currency is currency.normalize_currency
    assert cbr.CURRENCY_CODES is currency.CURRENCY_CODES
    assert iss.normalize_currency is currency.normalize_currency
    assert currency.CURRENCY_CODES == {"USD": "R01235", "EUR": "R01239", "CNY": "R01375",
                                       "CHF": "R01775", "GBP": "R01035"}
    assert [currency.normalize_currency(x) for x in ["SUR", None, float("nan"), "cny"]] == \
        ["RUB", "RUB", "RUB", "CNY"]


# ------------------------------------------------------------------ _board_quotes(secids): ЦБ только по нужным валютам
@pytest.fixture
def board_iss(monkeypatch, no_net):
    monkeypatch.setattr(iss, "get_json", fake_iss_get_json)
    f = FakeCBR()
    monkeypatch.setattr(cbr, "_fetch", f)
    return f


def test_board_quotes_secids_rub_only_no_cbr(board_iss):
    df = quotes._board_quotes("TQCB", "bonds", ["RU000ATEST01"])
    assert list(df["SECID"]) == ["RU000ATEST01"]
    assert df["price_rub"].iloc[0] == pytest.approx(990.0 + 21.78)
    assert board_iss.calls == []


def test_board_quotes_secids_only_needed_currency(board_iss):
    quotes._board_quotes("TQCB", "bonds", ["RU000A0JX2M2", "RU000ATEST01"])
    assert {p["VAL_NM_RQ"] for _, p in board_iss.calls} == {"R01235"}      # USD, без CNY


def test_board_quotes_without_secids_requests_all_currencies(board_iss):
    quotes._board_quotes("TQCB", "bonds")
    assert {p["VAL_NM_RQ"] for _, p in board_iss.calls} == {"R01235", "R01375"}


def test_get_quotes_rub_bond_portfolio_no_cbr(monkeypatch, board_iss):
    monkeypatch.setattr(iss, "resolve", _resolve_map({"RU000ATEST01": ("TQCB", "bonds"),
                                                      "SU26238RMFS4": ("TQOB", "bonds")}))
    q = quotes.get_quotes(["RU000ATEST01", "SU26238RMFS4"])
    assert board_iss.calls == []
    assert q.loc["RU000ATEST01", "price"] == pytest.approx(1011.78)
    assert q.loc["SU26238RMFS4", "price"] == pytest.approx(635.62)


# ------------------------------------------------------------------ cbr: таймаут и отрицательный кэш
class _Resp:
    def __init__(self, text):
        self.content, self.encoding = text.encode("windows-1251"), "windows-1251"

    def raise_for_status(self):
        pass


@pytest.fixture
def cbr_tmp_cache(tmp_path, monkeypatch):
    """Настоящий cbr._fetch, но HTTP-кэш — во временной директории, сеть — подменённая сессия."""
    from core.data.cache import HttpCache
    monkeypatch.delenv("IP_OFFLINE", raising=False)      # офлайн-режим проверяется отдельно
    monkeypatch.setattr(cbr, "_cache", HttpCache(tmp_path / "cache.sqlite"))
    calls = []
    state = {"down": True}

    def get(url, params=None, timeout=None, **k):
        calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if state["down"]:
            import requests
            raise requests.ConnectionError("нет сети (тест)")
        return _Resp((FIX / "cbr_usd_dynamic.xml").read_text(encoding="utf-8"))
    monkeypatch.setattr(cbr._s, "get", get)
    return calls, state


def test_cbr_negative_cache(cbr_tmp_cache, monkeypatch, caplog):
    calls, state = cbr_tmp_cache
    with pytest.raises(cbr.CBRUnavailable):
        cbr.currency_rate("USD", "2026-10-01", "2026-10-13")
    assert len(calls) == 1
    assert calls[0]["timeout"] == cbr.FX_TIMEOUT
    assert calls[0]["url"].endswith(cbr.FX_PATH)
    # в пределах FX_DOWN_TTL остальные валюты в сеть не ходят
    with pytest.raises(cbr.CBRUnavailable):
        cbr.currency_rate("EUR", "2026-10-01", "2026-10-13")
    with caplog.at_level("WARNING", logger="core.data.cbr"):
        r = cbr.rates_on({"CNY", "CHF", "SUR"}, "2026-10-13")
    assert math.isnan(r["CNY"]) and math.isnan(r["CHF"]) and r["RUB"] == 1.0
    assert len(calls) == 1
    assert any("недоступен" in x.getMessage() for x in caplog.records)
    # CBRUnavailable — подкласс ConnectionError (старые обработчики продолжают работать)
    assert issubclass(cbr.CBRUnavailable, ConnectionError)
    # по истечении FX_DOWN_TTL — снова запрос; сеть поднялась → курс получен
    import core.data.cache as cache_mod
    real_time = cache_mod.time.time
    monkeypatch.setattr(cache_mod.time, "time", lambda: real_time() + cbr.FX_DOWN_TTL + 1)
    state["down"] = False
    assert cbr.currency_rate_on("USD", "2026-10-12") == pytest.approx(83.5)
    assert len(calls) == 2 and calls[1]["timeout"] == cbr.FX_TIMEOUT


def test_cbr_negative_cache_not_set_by_other_requests(cbr_tmp_cache):
    calls, state = cbr_tmp_cache
    import requests
    with pytest.raises(requests.ConnectionError) as ei:
        cbr.gold_price("2026-10-01", "2026-10-13")         # не курсы — обычный таймаут, без метки
    assert not isinstance(ei.value, cbr.CBRUnavailable)
    assert calls[0]["timeout"] == 60
    state["down"] = False
    assert cbr.currency_rate_on("USD", "2026-10-13") == pytest.approx(USD)


# ------------------------------------------------------------------ rates_on: причины в логе
def test_rates_on_logs_reason(monkeypatch, no_net, caplog):
    monkeypatch.setattr(cbr, "_fetch", FakeCBR(fail_vids={"R01239"}))        # EUR — ЦБ недоступен
    with caplog.at_level("WARNING", logger="core.data.cbr"):
        r = cbr.rates_on(["XXX", "GBP", "EUR", "USD"], "2026-10-13")         # GBP — пустой ответ
    assert r["USD"] == pytest.approx(USD)
    assert all(math.isnan(r[c]) for c in ["XXX", "GBP", "EUR"])
    msg = {c: [x.getMessage() for x in caplog.records if x.name == "core.data.cbr" and c in x.getMessage()]
           for c in ["XXX", "GBP", "EUR", "USD"]}
    assert len(msg["XXX"]) == 1 and "неизвестная валюта" in msg["XXX"][0]
    assert len(msg["GBP"]) == 1 and "не публиковал" in msg["GBP"][0]
    assert len(msg["EUR"]) == 1 and "недоступен" in msg["EUR"][0]
    assert msg["USD"] == []
    assert all(x.levelname == "WARNING" for x in caplog.records if x.name == "core.data.cbr")


def test_rates_on_unknown_currency_no_request(monkeypatch, no_net):
    f = FakeCBR()
    monkeypatch.setattr(cbr, "_fetch", f)
    assert math.isnan(cbr.rates_on(["XXX"], "2026-10-13")["XXX"])
    assert f.calls == []


# ------------------------------------------------------------------ страницы 5 и 6: бумага без цены
def _tmp_ledger(tmp_path, monkeypatch):
    """Временная БД портфеля. Ledger() на страницах берёт путь из значения по умолчанию
    __init__(path=PORTFOLIO_DB), связанного при импорте, поэтому подмена config.PORTFOLIO_DB /
    IP_DATA_DIR на него не действует; подменяем значение по умолчанию через monkeypatch
    (восстанавливается автоматически, в том числе при падении теста)."""
    from core import config
    from core.portfolio import ledger as lg
    import requests

    def send(self, request, **kw):          # страницы в тестах не ходят в сеть
        raise AssertionError(f"сеть запрещена в тестах: {request.url}")
    monkeypatch.setattr(requests.Session, "send", send)
    db = tmp_path / "p.sqlite"
    monkeypatch.setattr(config, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg.Ledger.__init__, "__defaults__", (db,))
    return lg.Ledger(db)


def _fx_portfolio(tmp_path, monkeypatch):
    L = _tmp_ledger(tmp_path, monkeypatch)
    pid = L.create_portfolio("Основной", target={"SU26238RMFS4": 0.5, "RU000A0JX2M2": 0.5})
    L.add(pid, "2026-06-01", "DEPOSIT", amount=200_000)
    L.add(pid, "2026-06-01", "BUY", "SU26238RMFS4", 100, 600)
    L.add(pid, "2026-06-01", "BUY", "RU000A0JX2M2", 1, 84_000)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [635.62, np.nan], "name": ["ОФЗ 26238", "ГазКЗ-27Д"], "lot": [1, 1],
         "board": ["TQOB", "TQCB"], "market": ["bonds", "bonds"], "currency": ["RUB", "USD"]},
        index=["SU26238RMFS4", "RU000A0JX2M2"]))
    idx = pd.bdate_range("2026-06-01", "2026-10-13")
    px = pd.DataFrame({"SU26238RMFS4": np.linspace(600, 607, len(idx)), "RU000A0JX2M2": np.nan}, index=idx)
    monkeypatch.setattr(quotes, "price_history_rub", lambda s, start, end=None: px)
    monkeypatch.setenv("IP_OFFLINE", "1")
    return L


def _run(page):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    root = Path(__file__).resolve().parent.parent
    at = st_testing.AppTest.from_file(str(root / "pages" / page), default_timeout=120)
    at.run()
    return at


def test_portfolio_page_missing_price(tmp_path, monkeypatch):
    _fx_portfolio(tmp_path, monkeypatch)
    at = _run("5_Мой_портфель.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    w = [x.value for x in at.warning if "RU000A0JX2M2" in x.value]
    assert w and "нет курса ЦБ" in w[0]
    xirr = [mt for mt in at.metric if mt.label.startswith("XIRR")]
    assert xirr and xirr[0].value == "—"
    infos = [i.value for i in at.info]
    assert any("TWR, метрики" in i and "не рассчитаны" in i for i in infos), infos
    assert any("Сравнение не рассчитано" in i for i in infos), infos


def test_rebalance_page_missing_price(tmp_path, monkeypatch):
    _fx_portfolio(tmp_path, monkeypatch)
    at = _run("6_Ребалансировки.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    w = [x.value for x in at.warning if "RU000A0JX2M2" in x.value]
    assert w and "нет курса ЦБ" in w[0]
    assert any(i.value.startswith("Заявки не рассчитаны") for i in at.info), [i.value for i in at.info]
    # единственная таблица — отклонения, и в ней только оценённые бумаги; таблицы заявок нет
    drift = [d.value for d in at.dataframe if "Факт" in d.value.columns]
    assert len(drift) == 1 and list(drift[0].index) == ["SU26238RMFS4"]
    assert not any("Действие" in d.value.columns for d in at.dataframe)


# ------------------------------------------------------------------ cbr: HTTPError и офлайн-режим
def test_cbr_http_error_no_negative_cache(cbr_tmp_cache, monkeypatch):
    import requests
    calls, state = cbr_tmp_cache

    class R503:
        status_code = 503

        def raise_for_status(self):
            raise requests.HTTPError("503 Server Error: Service Unavailable", response=self)

    monkeypatch.setattr(cbr._s, "get", lambda url, params=None, timeout=None, **k:
                        calls.append({"url": url, "timeout": timeout}) or R503())
    with pytest.raises(requests.HTTPError):
        cbr.currency_rate("USD", "2026-10-01", "2026-10-13")
    assert cbr._cache.get(cbr._FX_DOWN_KEY, None) is None           # метки нет
    with pytest.raises(requests.HTTPError):                          # следующий запрос снова идёт в сеть
        cbr.currency_rate("EUR", "2026-10-01", "2026-10-13")
    assert len(calls) == 2


def test_cbr_offline_mode(cbr_tmp_cache, monkeypatch, caplog):
    calls, state = cbr_tmp_cache
    state["down"] = False
    # онлайн: курс на 12.10 попадает в кэш
    assert cbr.currency_rate_on("USD", "2026-10-12") == pytest.approx(83.5)
    assert len(calls) == 1
    monkeypatch.setenv("IP_OFFLINE", "1")
    # офлайн: закэшированный курс читается без сети
    assert cbr.currency_rate_on("USD", "2026-10-12") == pytest.approx(83.5)
    assert cbr.rates_on({"USD"}, "2026-10-12")["USD"] == pytest.approx(83.5)
    # незакэшированный запрос — NaN без сети, в лог info (не warning), метки отрицательного кэша нет
    with caplog.at_level("INFO", logger="core.data.cbr"):
        r = cbr.rates_on({"USD", "CNY", "SUR"}, "2026-10-13")
    assert math.isnan(r["USD"]) and math.isnan(r["CNY"]) and r["RUB"] == 1.0
    assert len(calls) == 1
    recs = [x for x in caplog.records if x.name == "core.data.cbr"]
    assert recs and all(x.levelname == "INFO" for x in recs)
    with pytest.raises(cbr.CBROffline):
        cbr.currency_rate("EUR", "2026-10-01", "2026-10-13")
    assert issubclass(cbr.CBROffline, cbr.CBRUnavailable)
    assert cbr._cache.get(cbr._FX_DOWN_KEY, None) is None
    assert len(calls) == 1


# ------------------------------------------------------------------ страницы 5 и 6: доп. сценарии (демо-период 2024–2025)
def test_rebalance_page_target_typo(tmp_path, monkeypatch):
    """В целях тикер-опечатка SBERR: котировки нет → предупреждение «нет котировки», заявок нет."""
    L = _tmp_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("IP_OFFLINE", "1")
    pid = L.create_portfolio("Основной", target={"EQMX": 0.5, "SBERR": 0.5})
    L.add(pid, "2024-01-15", "DEPOSIT", amount=100_000)
    L.add(pid, "2024-01-15", "BUY", "EQMX", 300, 150)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [180.0], "name": ["EQMX"], "lot": [1], "board": ["TQTF"], "market": ["shares"],
         "currency": ["RUB"]}, index=["EQMX"]))
    at = _run("6_Ребалансировки.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    w = [x.value for x in at.warning if "SBERR" in x.value]
    assert w and "SBERR (нет котировки" in w[0]
    assert any(i.value.startswith("Заявки не рассчитаны") for i in at.info)
    assert not any("Действие" in d.value.columns for d in at.dataframe)
    drift = [d.value for d in at.dataframe if "Факт" in d.value.columns]
    assert drift and list(drift[0].index) == ["EQMX"]


def test_portfolio_page_sold_before_gap_not_warned(tmp_path, monkeypatch):
    """OLD продана в 2024 г. и больше не котируется; у USD1 (куплена в 2025 г.) нет курса ЦБ.
    Предупреждения — только про USD1, OLD в них не упоминается."""
    L = _tmp_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("IP_OFFLINE", "1")
    pid = L.create_portfolio("Основной")
    L.add(pid, "2024-01-15", "DEPOSIT", amount=200_000)
    L.add(pid, "2024-01-15", "BUY", "EQMX", 300, 150)
    L.add(pid, "2024-01-15", "BUY", "OLD", 10, 1000)
    L.add(pid, "2024-06-03", "SELL", "OLD", 10, 1010)
    L.add(pid, "2025-01-10", "BUY", "USD1", 1, 84_000)
    idx = pd.bdate_range("2024-01-01", "2025-12-31")
    px = pd.DataFrame({"EQMX": 150 * np.exp(np.linspace(0, .2, len(idx))), "USD1": np.nan,
                       "OLD": np.where(idx <= "2024-06-03", 1000.0, np.nan)}, index=idx)
    monkeypatch.setattr(quotes, "price_history_rub", lambda s, start, end=None: px)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [180.0, np.nan], "name": ["EQMX", "USD1"], "lot": [1, 1], "board": ["TQTF", "TQCB"],
         "market": ["shares", "bonds"], "currency": ["RUB", "USD"]}, index=["EQMX", "USD1"]))
    at = _run("5_Мой_портфель.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    warns = [x.value for x in at.warning]
    assert any("USD1" in x for x in warns), warns
    assert not any("OLD" in x for x in warns), warns
    assert any("не рассчитаны" in i.value for i in at.info)


def test_portfolio_page_sold_bond_no_gap_builds_twr(tmp_path, monkeypatch):
    """Только проданная OLD без текущей котировки: предупреждений нет, TWR строится."""
    L = _tmp_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("IP_OFFLINE", "1")
    pid = L.create_portfolio("Основной")
    L.add(pid, "2024-01-15", "DEPOSIT", amount=200_000)
    L.add(pid, "2024-01-15", "BUY", "EQMX", 300, 150)
    L.add(pid, "2024-01-15", "BUY", "OLD", 10, 1000)
    L.add(pid, "2024-06-03", "SELL", "OLD", 10, 1010)
    idx = pd.bdate_range("2024-01-01", "2025-12-31")
    px = pd.DataFrame({"EQMX": 150 * np.exp(np.linspace(0, .2, len(idx))),
                       "OLD": np.where(idx <= "2024-06-03", 1000.0, np.nan)}, index=idx)
    monkeypatch.setattr(quotes, "price_history_rub", lambda s, start, end=None: px)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [180.0], "name": ["EQMX"], "lot": [1], "board": ["TQTF"], "market": ["shares"],
         "currency": ["RUB"]}, index=["EQMX"]))
    at = _run("5_Мой_портфель.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert not at.warning, [x.value for x in at.warning]
    assert not any("не рассчитаны" in i.value for i in at.info)
    xirr = [mt for mt in at.metric if mt.label.startswith("XIRR")]
    assert xirr and xirr[0].value != "—"
