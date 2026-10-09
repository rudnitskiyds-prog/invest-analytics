"""Страница бумаги в Streamlit: core/analytics/symbol_page.py, новые функции core/data/iss.py и public_data,
страницы pages/2_Карточка_бумаги.py, Витрина (колонки рейтинга), Обзор (лидеры), Бэктест (bt_prefill).

Сеть запрещена: ISS подменяется на уровне сессии requests (iss._session) — синтетикой tests/iss_fake.py
или маленькими таблицами, заданными прямо в тесте; HTTP-кэш ISS — пустышка; public/data — во временном
каталоге (кроме тестов паритета с JS, которые читают реальные файлы сборщика репозитория).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pickle
import re
import shutil
import subprocess
import types
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import numpy as np
import pandas as pd
import pytest
import requests

from core import config
from core.analytics import metrics as m
from core.analytics import symbol_page as sp
from core.data import iss, public_data
from tests import iss_fake

ROOT = Path(__file__).resolve().parent.parent
REAL_PUBLIC = ROOT / "public" / "data"


# ====================================================================== подмена ISS
class _Resp:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Not Found")

    def json(self):
        return self._body


class FakeSession:
    """Сессия requests: respond(url) -> (status, body) | None (неизвестный маршрут -> 404, пишется в unknown)."""

    def __init__(self, respond, fail: bool = False):
        self.respond, self.fail = respond, fail
        self.calls: list[str] = []
        self.unknown: list[str] = []

    def get(self, url, params=None, timeout=None, **kw):
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append(full)
        if self.fail:
            raise requests.ConnectionError("Max retries exceeded (сеть отключена в тестах)")
        res = self.respond(full)
        if res is None:
            self.unknown.append(full)
            return _Resp(404, {})
        return _Resp(*res)


class _NoCache:
    def get(self, k, ttl):
        return None

    def put(self, k, body):
        pass


@pytest.fixture
def use_iss(monkeypatch):
    """use_iss(respond | None, fail=False) -> FakeSession. По умолчанию — синтетика tests/iss_fake.py."""
    monkeypatch.setattr(iss, "_cache", _NoCache())
    monkeypatch.setattr(iss, "time", types.SimpleNamespace(sleep=lambda *_: None, time=lambda: 0.0))

    def install(respond=None, fail=False):
        s = FakeSession(respond or iss_fake.respond, fail)
        monkeypatch.setattr(iss, "_session", s)
        return s
    yield install
    iss.resolve.cache_clear()


def _q(url: str) -> tuple[str, dict]:
    u = urlparse(url)
    return unquote(u.path), {k: v[-1] for k, v in parse_qs(u.query).items()}


# ====================================================================== public/data во временном каталоге
def _write(d: Path, name: str, obj):
    (d / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


DIVS_SBER = [
    {"record_date": "2023-05-11", "last_buy_date": "2023-05-10", "payment_date": "2023-05-20",
     "declared_date": "2023-04-01", "value": 25.0, "currency": "RUB", "yield_value": 10.0, "cancelled": False},
    {"record_date": "2024-07-11", "last_buy_date": "2024-07-10", "value": 33.3, "currency": "RUB",
     "yield_value": 10.5, "cancelled": False},
    {"record_date": "2025-07-18", "last_buy_date": "2025-07-17", "value": 34.84, "currency": "RUB",
     "yield_value": 11.0, "cancelled": False},
    {"record_date": "2025-08-01", "value": 1.0, "currency": "USD", "cancelled": False},          # не рубли
    {"record_date": "2025-09-01", "last_buy_date": "2025-08-29", "value": 5.0, "currency": "RUB",
     "cancelled": True},                                                                        # отменён
    {"record_date": None, "value": 1.0},                                                       # без даты — отброшен
]
FUNDS = [
    {"ticker": "LQDT", "issuer": "ВИМ Инвестиции", "asset_class": "Деньги", "commission_pct": 0.4,
     "aum_rub": 5e11, "trade_status": "Торгуется"},
    {"ticker": "AKMM", "issuer": "Альфа-Капитал", "asset_class": "Деньги", "commission_pct": 0.6,
     "trade_status": "Торгуется"},
    {"ticker": "SBMM", "issuer": "Первая", "asset_class": "Деньги", "commission_pct": 0.8},
    {"ticker": "OLDM", "issuer": "X", "asset_class": "Деньги", "commission_pct": 5.0, "trade_status": "Закрыт"},
    {"ticker": "TMOS", "issuer": "Т-Капитал", "asset_class": "Акции", "commission_pct": 0.79,
     "trade_status": "Торгуется"},
    {"ticker": "FREE", "issuer": "Y", "asset_class": "Акции", "commission_pct": 0, "trade_status": "Торгуется"},
]


@pytest.fixture
def pub(tmp_path, monkeypatch):
    """Временный public/data: дивиденды SBER, 6 фондов RusETFs, синтетический symbol_stats."""
    d = tmp_path / "public_data"
    d.mkdir()
    _write(d, "dividends", {"updated": "2026-10-01", "data": {"SBER": DIVS_SBER}})
    _write(d, "rusetfs_funds", {"updated": "2026-10-01", "data": FUNDS})
    (d / "symbol_stats.json").write_text(json.dumps(iss_fake.symbol_stats(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", d)
    return d


@pytest.fixture
def no_pub(tmp_path, monkeypatch):
    d = tmp_path / "empty_public"
    d.mkdir()
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", d)
    return d


# ====================================================================== classify / default_benchmark
@pytest.mark.parametrize("kw, cls", [
    (dict(type_="common_share", group="stock_shares"), "share"),
    (dict(type_="preferred_share"), "share"),
    (dict(group="stock_dr"), "share"),
    (dict(sectype="D"), "share"),
    (dict(type_="exchange_ppif", group="stock_ppif"), "fund"),
    (dict(sectype="J"), "fund"),
    (dict(type_="ofz_bond"), "bond"),
    (dict(market="bonds"), "bond"),
    (dict(type_="stock_index"), "index"),
    (dict(market="index"), "index"),
    (dict(secid="GLDRUB_TOM"), "metal"),
    (dict(group="currency_selt"), "currency"),
    (dict(), None),
])
def test_classify(kw, cls):
    assert sp.classify(**kw) == cls


@pytest.mark.parametrize("cls, secid, type_, bench", [
    ("share", "SBER", None, "MCFTR"), ("fund", "LQDT", None, "MCFTR"),
    ("bond", "SU26238RMFS4", "ofz_bond", "RGBITR"), ("bond", "SU26238RMFS4", None, "RGBITR"),
    ("bond", "RU000A105ZN0", "corporate_bond", "RUCBTRNS"),
    ("index", "MCFTR", None, "IMOEX"), ("index", "IMOEX", None, "MCFTR"),
    ("metal", "GLDRUB_TOM", None, "GOLD_CBR"), ("currency", "USD000UTSTOM", None, None), (None, "X", None, None),
])
def test_default_benchmark(cls, secid, type_, bench):
    assert sp.default_benchmark(cls, secid, type_) == bench


# ====================================================================== daily_ohlc: подклейка режимов
def _candles_block(rows: dict, q: dict) -> dict:
    """rows: {'YYYY-MM-DD': close | (open, high, low, close)} -> блок candles в окне from/till."""
    out = []
    for d, v in sorted(rows.items()):
        if q.get("from") and d < q["from"][:10] or q.get("till") and d > q["till"][:10]:
            continue
        o, h, lo, c = v if isinstance(v, tuple) else (v, v * 1.01, v * 0.99, v)
        out.append([o, c, h, lo, c * 10, 10, f"{d} 00:00:00", f"{d} 23:59:59"])
    if int(q.get("start") or 0) > 0:
        out = []
    return {"candles": iss_fake.block(["open", "close", "high", "low", "value", "volume", "begin", "end"], out)}


def board_world(secid: str, boards: list[tuple], candles: dict, history: dict | None = None,
                borders: list | None | str = None):
    """Маленький ISS: boards — (boardid, market, is_primary, history_from, history_till, board_group_id);
    candles — {board | 'index': rows}; history — {date: close} (индексы); borders — строки candleborders
    или '404'."""
    def respond(url):
        path, q = _q(url)
        if path == f"/iss/securities/{secid}.json":
            return 200, {"description": iss_fake.block(["name", "title", "value"], [("SECID", "", secid)]),
                         "boards": iss_fake.block(
                             ["secid", "boardid", "market", "engine", "is_primary", "history_from", "history_till",
                              "board_group_id"],
                             [(secid, b, mk, "stock", p, hf, ht, g) for b, mk, p, hf, ht, g in boards])}
        mt = re.fullmatch(rf"/iss/engines/stock/markets/[^/]+/boards/([^/]+)/securities/{secid}/candles\.json", path)
        if mt:
            return 200, _candles_block(candles.get(mt.group(1), {}), q)
        if path == f"/iss/engines/stock/markets/index/securities/{secid}/candles.json":
            return 200, _candles_block(candles.get("index", {}), q)
        if path == f"/iss/engines/stock/markets/index/securities/{secid}/candleborders.json":
            if borders == "404" or borders is None:
                return None
            return 200, {"borders": iss_fake.block(["begin", "end", "interval", "board_group_id"], borders)}
        if path == f"/iss/history/engines/stock/markets/index/securities/{secid}.json":
            rows = [(secid, d, c - 1, c + 1, c - 2, c) for d, c in sorted((history or {}).items())
                    if (not q.get("from") or d >= q["from"]) and (not q.get("till") or d <= q["till"])]
            start = int(q.get("start") or 0)
            return 200, {"history": iss_fake.block(["SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE"],
                                                   rows[start:start + 100]),
                         "history.cursor": iss_fake.block(["INDEX", "TOTAL", "PAGESIZE"], [(start, len(rows), 100)])}
        return None
    return respond


SHARE_BOARDS = [
    ("TQBR", "shares", 1, "2013-03-25", "2026-10-08", 57),
    ("EQBR", "shares", 0, "2013-03-18", "2013-03-25", 6),     # прежний основной режим, общая дата 25.03
    ("XXSG", "shares", 0, "2013-03-01", "2013-03-15", 57),    # не из списка, но та же board_group_id
    ("SMAL", "shares", 0, "2013-03-01", "2013-03-15", 99),    # чужая группа — не подклеивается
    ("EQOB", "bonds", 0, "2013-03-01", "2013-03-15", 7),      # другой рынок — не подклеивается
]
SHARE_CANDLES = {
    "EQBR": {"2013-03-18": 10.0, "2013-03-19": 11.0, "2013-03-25": 99.0},
    "XXSG": {"2013-03-01": 5.0, "2013-03-15": 6.0},
    "SMAL": {"2013-03-04": 500.0},
    "EQOB": {"2013-03-05": 700.0},
    "TQBR": {"2013-03-25": 12.0, "2013-03-26": 13.0, "2013-03-27": 0.0,
             "2013-03-28": (0.0, 15.0, -1.0, 14.0)},
}


def test_daily_ohlc_glues_legacy_board_primary_wins(use_iss):
    s = use_iss(board_world("TSTA", SHARE_BOARDS, SHARE_CANDLES))
    df = iss.daily_ohlc("TSTA", "1990-01-01", "2013-12-31")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert [str(d.date()) for d in df.index] == ["2013-03-01", "2013-03-15", "2013-03-18", "2013-03-19",
                                                "2013-03-25", "2013-03-26", "2013-03-28"]
    assert df["close"].tolist() == [5.0, 6.0, 10.0, 11.0, 12.0, 13.0, 14.0]   # 25.03 — из TQBR, не 99 из EQBR
    # строка с close = 0 отброшена; open = 0 и low < 0 -> NaN
    row = df.loc["2013-03-28"]
    assert math.isnan(row["open"]) and math.isnan(row["low"]) and row["high"] == 15.0
    assert df.attrs.get("split_adjusted") is True
    assert df.index.is_monotonic_increasing and df.index.name == "date"
    boards = {re.search(r"/boards/([^/]+)/", u).group(1) for u in s.calls if "/candles" in u}
    assert boards == {"TQBR", "EQBR", "XXSG"}
    eqbr = [_q(u)[1] for u in s.calls if "/boards/EQBR/" in u][0]
    assert eqbr["till"] == "2013-03-25"


def test_daily_ohlc_start_inside_legacy_segment(use_iss):
    use_iss(board_world("TSTA", SHARE_BOARDS, SHARE_CANDLES))
    df = iss.daily_ohlc("TSTA", "2013-03-19", "2013-12-31")
    assert df["close"].tolist() == [11.0, 12.0, 13.0, 14.0]


def test_daily_ohlc_fund_tqtf_to_tqbr(use_iss):
    boards = [("TQBR", "shares", 1, "2026-06-10", "2026-10-08", 57),
              ("TQTF", "shares", 0, "2020-01-20", "2026-06-10", 9)]
    candles = {"TQTF": {"2026-06-08": 1.0, "2026-06-09": 1.1, "2026-06-10": 9.9},
               "TQBR": {"2026-06-10": 1.2, "2026-06-11": 1.3}}
    use_iss(board_world("TSTF", boards, candles))
    df = iss.daily_ohlc("TSTF", "2026-01-01", "2026-07-01")
    assert df["close"].tolist() == [1.0, 1.1, 1.2, 1.3]


def test_daily_ohlc_index_candleborders_then_history(use_iss):
    hist = {"2017-12-28": 1000.0, "2017-12-29": 1001.0, "2018-01-03": 1.0}
    cand = {"index": {"2018-01-03": (1999.0, 2010.0, 1990.0, 2000.0), "2018-01-04": 2001.0}}
    borders = [("2018-01-03 00:00:00", "2026-10-08 18:50:00", 24, 9), ("2011-12-01 00:00:00", "2026-10-08", 31, 9)]
    s = use_iss(board_world("TIDX", [("SNDX", "index", 1, "2013-01-08", "2026-10-08", 9)], cand, hist, borders))
    df = iss.daily_ohlc("TIDX", "2017-01-01", "2018-12-31")
    assert [str(d.date()) for d in df.index] == ["2017-12-28", "2017-12-29", "2018-01-03", "2018-01-04"]
    assert df["close"].tolist() == [1000.0, 1001.0, 2000.0, 2001.0]
    assert df["volume"].isna().all()
    assert df.loc["2017-12-28", "high"] == 1001.0 and df.loc["2018-01-03", "low"] == 1990.0
    h = [_q(u)[1] for u in s.calls if "/history/" in u]
    assert h and all(q["till"] == "2018-01-02" for q in h)
    c = [_q(u)[1] for u in s.calls if u.split("?")[0].endswith("/candles.json")]
    assert c and c[0]["from"] == "2018-01-03"


def test_daily_ohlc_index_start_after_begin_skips_history(use_iss):
    cand = {"index": {"2018-01-03": 2000.0, "2018-01-04": 2001.0}}
    borders = [("2018-01-03 00:00:00", "2026-10-08 18:50:00", 24, 9)]
    s = use_iss(board_world("TIDX", [("SNDX", "index", 1, "2013-01-08", "2026-10-08", 9)], cand,
                            {"2017-12-29": 1.0}, borders))
    df = iss.daily_ohlc("TIDX", "2018-01-04", "2018-12-31")
    assert df["close"].tolist() == [2001.0]
    assert not [u for u in s.calls if "/history/" in u]


@pytest.mark.parametrize("borders", ["404", [("2011-12-01 00:00:00", "2026-10-08", 31, 9)]])
def test_daily_ohlc_index_without_candleborders_history_only(use_iss, borders):
    hist = {"2017-12-29": 1001.0, "2018-01-03": 1002.0, "2026-01-05": 1003.0}
    s = use_iss(board_world("TIDX", [("SNDX", "index", 1, "2013-01-08", "2026-10-08", 9)],
                            {"index": {"2018-01-03": 2000.0}}, hist, borders))
    df = iss.daily_ohlc("TIDX", "2017-01-01", "2026-12-31")
    assert df["close"].tolist() == [1001.0, 1002.0, 1003.0]
    assert not [u for u in s.calls if u.split("?")[0].endswith("/candles.json")]
    # candleborders — одна попытка
    assert len([u for u in s.calls if "candleborders" in u]) == 1


def test_daily_ohlc_unknown_security_raises_iss_error(use_iss):
    use_iss()
    with pytest.raises(iss.ISSError):
        iss.daily_ohlc("NOSUCH")


def test_daily_ohlc_empty_frame_shape(use_iss):
    use_iss(board_world("TSTA", SHARE_BOARDS[:1], {}))
    df = iss.daily_ohlc("TSTA", "2020-01-01", "2020-02-01")
    assert df.empty and list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)


def test_index_candle_begin(use_iss):
    use_iss()
    assert iss.index_candle_begin("IMOEX") == iss_fake.INDEX_CANDLES_FROM
    assert iss.index_candle_begin("NOSUCH") is None


def test_description_issuer_board_snapshot(use_iss):
    use_iss()
    d = iss.description("SBER")
    assert d["description"]["NAME"] == "Сбербанк России ПАО ао"
    assert {b["boardid"] for b in d["boards"]} == {"TQBR", "EQBR"}
    assert iss.issuer("SBER") == "ПАО Сбербанк"
    assert iss.issuer("NOSUCH") is None
    snap = iss.board_snapshot("SU26238RMFS4", "stock", "bonds", "TQOB")
    assert set(snap) == {"securities", "marketdata", "marketdata_yields"}
    assert snap["marketdata"]["YIELD"] == 14.25 and snap["securities"]["ACCRUEDINT"] == 12.5
    snap = iss.board_snapshot("SBER", "stock", "shares", "TQBR")
    assert snap["marketdata_yields"] == {}


def test_issuer_iss_down_returns_none(use_iss):
    use_iss(fail=True)
    assert iss.issuer("SBER") is None


# ====================================================================== snapshot
def _ohlc(rows: dict) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(list(rows)), name="date")
    v = np.array(list(rows.values()), dtype=float)
    return pd.DataFrame({"open": v[:, 0], "high": v[:, 1], "low": v[:, 2], "close": v[:, 3],
                         "volume": np.nan}, index=idx)


def _patch_snapshot(monkeypatch, market="shares", blocks=None):
    monkeypatch.setattr(iss, "description", lambda s: {"description": {}, "boards": [
        {"boardid": "TQBR" if market != "index" else "SNDX", "engine": "stock", "market": market, "is_primary": 1}]})
    monkeypatch.setattr(iss, "board_snapshot", lambda *a: blocks or {"securities": {}, "marketdata": {},
                                                                      "marketdata_yields": {}})


def test_snapshot_fallback_to_candles_same_date(monkeypatch):
    _patch_snapshot(monkeypatch)
    o = _ohlc({"2025-01-09": (1, 2, 1, 100.0), "2025-01-10": (1, 2, 1, 110.0)})
    s = sp.snapshot("TEST", o)
    assert s["date"] == "2025-01-10" and s["last"] == 110.0
    assert s["prev_close"] == 100.0          # дата marketdata = дата последней свечи -> предпоследняя свеча
    assert s["change"] == pytest.approx(10.0) and s["change_pct"] == pytest.approx(0.1)
    assert s["market_cap"] is None and s["bond"] is None and s["value_rub"] is None


def test_snapshot_marketdata_newer_than_candles(monkeypatch):
    _patch_snapshot(monkeypatch, blocks={"securities": {"SECTYPE": "1", "ISSUESIZE": 1000},
                                         "marketdata": {"LAST": 121.0, "TRADEDATE": "2025-01-13",
                                                        "VALTODAY": 5e6}, "marketdata_yields": {}})
    o = _ohlc({"2025-01-09": (1, 2, 1, 100.0), "2025-01-10": (1, 2, 1, 110.0)})
    s = sp.snapshot("TEST", o)
    assert s["date"] == "2025-01-13" and s["last"] == 121.0
    assert s["prev_close"] == 110.0          # другая дата -> последняя свеча
    assert s["change_pct"] == pytest.approx(0.1)
    assert s["market_cap"] == pytest.approx(121_000.0)    # акция: LAST × ISSUESIZE
    assert s["value_rub"] == 5e6


def test_snapshot_prevprice_beats_candles_and_one_candle(monkeypatch):
    _patch_snapshot(monkeypatch, blocks={"securities": {"PREVPRICE": 50.0}, "marketdata": {}, "marketdata_yields": {}})
    s = sp.snapshot("TEST", _ohlc({"2025-01-10": (1, 2, 1, 55.0)}))
    assert s["last"] == 55.0 and s["prev_close"] == 50.0
    _patch_snapshot(monkeypatch)
    s = sp.snapshot("TEST", _ohlc({"2025-01-10": (1, 2, 1, 55.0)}))
    assert s["prev_close"] is None and s["change"] is None and s["change_pct"] is None


def test_snapshot_index_prev_and_cap(monkeypatch):
    _patch_snapshot(monkeypatch, market="index", blocks={"securities": {}, "marketdata": {
        "CURRENTVALUE": 2800.0, "LASTCHANGE": -28.0, "TRADEDATE": "2025-01-13", "CAPITALIZATION": 6.5e13},
        "marketdata_yields": {}})
    s = sp.snapshot("IMOEX", _ohlc({"2025-01-10": (1, 2, 1, 2700.0)}))
    assert s["last"] == 2800.0 and s["prev_close"] == 2828.0
    assert s["change_pct"] == pytest.approx(-28.0 / 2828.0)
    assert s["market_cap"] == 6.5e13


def test_snapshot_bond_fields(monkeypatch):
    _patch_snapshot(monkeypatch, market="bonds", blocks={
        "securities": {"PREVPRICE": 98.0, "ACCRUEDINT": 12.5, "COUPONVALUE": 35.4, "NEXTCOUPON": "2026-11-18"},
        "marketdata": {"LAST": 99.0, "DURATION": 730.5, "TRADEDATE": "2026-10-08"},
        "marketdata_yields": {"EFFECTIVEYIELD": 14.6, "DURATION": 1}})
    s = sp.snapshot("SU1", _ohlc({"2026-10-07": (1, 2, 1, 98.0)}))
    b = s["bond"]
    assert b["yield"] == pytest.approx(0.146)               # нет YIELD -> EFFECTIVEYIELD, в долях
    assert b["duration"] == pytest.approx(2.0)              # 730,5 дн. / 365,25
    assert b["accrued_int"] == 12.5 and b["coupon_value"] == 35.4 and b["next_coupon"] == "2026-11-18"
    assert s["market_cap"] is None


def test_snapshot_high_low_52_window_and_nan_fallback(monkeypatch):
    _patch_snapshot(monkeypatch)
    o = _ohlc({"2023-12-01": (1, 999.0, 0.1, 50.0),        # старше 365 дней — вне окна
               "2024-01-10": (1, np.nan, np.nan, 80.0),     # нет high/low -> close
               "2024-06-03": (1, 120.0, 70.0, 100.0),
               "2025-01-09": (1, 105.0, 90.0, 95.0)})
    s = sp.snapshot("TEST", o)
    assert s["high52"] == 120.0 and s["low52"] == 70.0
    o2 = _ohlc({"2024-01-09": (1, np.nan, np.nan, 60.0), "2025-01-09": (1, 105.0, 90.0, 95.0)})
    s2 = sp.snapshot("TEST", o2)
    assert s2["high52"] == 105.0 and s2["low52"] == 90.0     # 09.01.2024 — за 366 дней (високосный год): вне окна


def test_snapshot_no_ohlc_no_marketdata(monkeypatch):
    _patch_snapshot(monkeypatch)
    s = sp.snapshot("TEST", pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    assert s["last"] is None and s["high52"] is None and s["date"] is None


# ====================================================================== dividend_stats
def _d(ex, value, **kw):
    return {"ex_date": ex, "record_date": ex, "value": value, "currency": kw.pop("currency", "RUB"), **kw}


def _px(last_date, price=100.0):
    return pd.Series([price, price], index=pd.to_datetime([pd.Timestamp(last_date) - pd.Timedelta(days=1),
                                                           last_date]))


def test_dividend_stats_hand_example():
    divs = [_d("2019-07-01", 10), _d("2020-07-01", 12), _d("2021-05-01", 6), _d("2021-12-01", 9),
            _d("2021-06-01", 100, cancelled=True), _d("2021-06-02", 100, currency="USD"),
            _d("2022-06-01", 20), _d("2022-06-02", 7)]
    s = sp.dividend_stats(divs, _px("2022-06-01", 50.0))
    assert s["as_of"] == "2022-06-01"
    # TTM: (01.06.2021; 01.06.2022] -> 9 (01.12.2021) + 20 (01.06.2022); 01.05.2021 — вне окна
    assert s["ttm_value"] == pytest.approx(29.0)
    assert s["ttm_yield"] == pytest.approx(29.0 / 50.0)
    assert s["by_year"] == [{"year": 2019, "value": 10.0}, {"year": 2020, "value": 12.0},
                            {"year": 2021, "value": 15.0}, {"year": 2022, "value": 20.0}]
    assert s["by_month"] == [0, 0, 0, 0, 1, 1, 2, 0, 0, 0, 0, 1]
    assert s["growth_streak"] == 2            # 2021 > 2020 > 2019; 2019 — первый год, ростом не считается
    assert s["payouts_per_year"] == pytest.approx((1 + 1 + 2) / 3)
    assert [u["ex_date"] for u in s["upcoming"]] == ["2022-06-02"]


def test_dividend_stats_ttm_boundary_exactly_365_days():
    s = sp.dividend_stats([_d("2021-06-01", 1), _d("2021-06-02", 2)], None, as_of="2022-06-01")
    assert s["ttm_value"] == 2.0 and s["ttm_yield"] is None


@pytest.mark.parametrize("totals, as_of, streak", [
    ({2021: [5]}, "2022-03-01", 0),                     # только год первой выплаты
    ({2020: [5], 2021: [6]}, "2022-03-01", 1),
    ({2018: [5], 2020: [6], 2021: [7]}, "2022-03-01", 2),   # 2019 без выплат = 0 -> 2020 рост к 0, 2019 к 2018 — нет
    ({2019: [5], 2020: [6], 2021: [6]}, "2022-03-01", 0),   # равенство — не рост
    ({2019: [5], 2020: [6], 2021: [7], 2022: [1]}, "2022-12-31", 2),   # текущий год не считается
    ({2019: [5], 2020: [6]}, "2022-03-01", 0),          # 2021 без выплат -> серия прервана
])
def test_dividend_stats_growth_streak(totals, as_of, streak):
    divs = [_d(f"{y}-0{i + 3}-15", v) for y, vs in totals.items() for i, v in enumerate(vs)]
    assert sp.dividend_stats(divs, None, as_of=as_of)["growth_streak"] == streak


def test_dividend_stats_payouts_per_year_not_before_first_payment():
    divs = [_d("2021-03-01", 1), _d("2021-09-01", 1)]
    s = sp.dividend_stats(divs, None, as_of="2022-02-01")
    assert s["payouts_per_year"] == 2.0          # только 2021 (2019 и 2020 — до первой выплаты)
    assert sp.dividend_stats(divs, None, as_of="2021-10-01")["payouts_per_year"] is None


def test_dividend_stats_empty():
    s = sp.dividend_stats([], None)
    assert s["ttm_value"] == 0.0 and s["by_year"] == [] and s["growth_streak"] == 0
    assert s["payouts_per_year"] is None and s["upcoming"] == [] and s["by_month"] == [0] * 12
    assert s["as_of"] == dt.date.today().isoformat()


def test_dividend_stats_last_price_skips_nonpositive():
    px = pd.Series([40.0, np.nan, 0.0], index=pd.to_datetime(["2022-05-30", "2022-05-31", "2022-06-01"]))
    s = sp.dividend_stats([_d("2022-05-01", 4)], px)
    assert s["as_of"] == "2022-06-01" and s["ttm_yield"] == pytest.approx(0.1)


# ---------------------------------------------------------------------- паритет с JS (node)
def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден: паритет с web/lib/symbol.js не проверяется")
    return node


def _run_parity(task: dict, tmp_path) -> dict:
    p = tmp_path / "task.json"
    p.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([_node(), "tests/web/symbol_parity.mjs", str(p)], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


PARITY_TICKERS = ["SBER", "GAZP", "LKOH", "MTSS", "CHMF", "TATNP", "AKRN", "T", "RUAL", "HHRU", "MOEX", "NOSUCH"]
PARITY_DATES = ["2012-06-29", "2019-12-31", "2022-03-01", "2024-07-15", "2026-10-02"]


def _close_for(as_of):
    d = pd.bdate_range(end=as_of, periods=5)
    vals = [100.0, 101.0, 102.0, 0.0, 103.0 + d[-1].month]  # ноль — отбрасывается при поиске последней цены
    return d, vals


def test_dividend_stats_parity_with_js(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", REAL_PUBLIC)
    if not (REAL_PUBLIC / "dividends.json").exists():
        pytest.skip("нет public/data/dividends.json")
    cases = [{"secid": s, "dates": None, "values": None} for s in PARITY_TICKERS]
    for s in PARITY_TICKERS:
        for as_of in PARITY_DATES:
            d, v = _close_for(as_of)
            cases.append({"secid": s, "dates": [str(x.date()) for x in d], "values": v})
    js = _run_parity({"dataDir": str(REAL_PUBLIC), "dividends": cases}, tmp_path)["dividends"]
    assert len(js) == len(cases)
    checked = 0
    for c, j in zip(cases, js):
        py_divs = sp.load_dividends(c["secid"])
        # loadDividends
        assert len(py_divs) == len(j["divs"]), c["secid"]
        for a, b in zip(py_divs, j["divs"]):
            assert (a["record_date"], a["ex_date"], a["payment_date"], a["declared_date"], a["currency"],
                    a["cancelled"]) == (b["recordDate"], b["exDate"], b["paymentDate"], b["declaredDate"],
                                        b["currency"], b["cancelled"]), c["secid"]
            assert a["value"] == pytest.approx(b["value"])
            assert (a["yield"] is None) == (b["yield"] is None)
            if a["yield"] is not None:
                assert a["yield"] == pytest.approx(b["yield"], rel=1e-12)
        # dividendStats
        close = pd.Series(c["values"], index=pd.to_datetime(c["dates"])) if c["dates"] else None
        py = sp.dividend_stats(py_divs, close)
        st = j["stats"]
        msg = f"{c['secid']} на {py['as_of']}"
        assert py["ttm_value"] == pytest.approx(st["ttmValue"], abs=1e-9), msg
        if st["ttmYield"] is None:
            assert py["ttm_yield"] is None, msg
        else:
            assert py["ttm_yield"] == pytest.approx(st["ttmYield"], rel=1e-12), msg
        assert [(r["year"], pytest.approx(r["value"])) for r in py["by_year"]] == \
            [(r["year"], r["value"]) for r in st["byYear"]], msg
        assert py["by_month"] == st["byMonth"], msg
        assert py["growth_streak"] == st["growthStreak"], msg
        assert (py["payouts_per_year"] is None) == (st["payoutsPerYear"] is None), msg
        if st["payoutsPerYear"] is not None:
            assert py["payouts_per_year"] == pytest.approx(st["payoutsPerYear"]), msg
        assert [(u["ex_date"], u["value"]) for u in py["upcoming"]] == \
            [(u["exDate"], u["value"]) for u in st["upcoming"]], msg
        checked += bool(py["by_year"])
    assert checked >= 30          # большая часть случаев — с реальными выплатами


# ====================================================================== fund_info
def test_fund_info_hand_example(pub):
    fi = sp.fund_info("lqdt")
    assert fi["info"]["ticker"] == "LQDT" and fi["info"]["issuer"] == "ВИМ Инвестиции"
    mk = fi["market"]
    # класс «Деньги», торгуемые (OLDM закрыт; SBMM без статуса — торгуется): 0,4; 0,6; 0,8
    assert mk["n"] == 3 and mk["min"] == 0.4 and mk["max"] == 0.8
    assert mk["median"] == pytest.approx(0.6) and mk["class_median"] == pytest.approx(0.6)
    assert mk["p25"] == pytest.approx(0.5) and mk["p75"] == pytest.approx(0.7)
    # все торгуемые с комиссией > 0: 0,4; 0,6; 0,79; 0,8 -> медиана 0,695
    assert mk["market_median"] == pytest.approx(0.695)


def test_fund_info_unknown_ticker_uses_all_traded(pub):
    fi = sp.fund_info("NOPE")
    assert fi["info"] is None and fi["market"]["n"] == 4
    assert fi["market"]["median"] == pytest.approx(0.695)


def test_fund_info_info_is_copy(pub):
    sp.fund_info("LQDT")["info"]["ticker"] = "HACK"
    assert sp.fund_info("LQDT")["info"]["ticker"] == "LQDT"


def test_fund_info_no_file(no_pub):
    fi = sp.fund_info("LQDT")
    assert fi["info"] is None and fi["market"]["n"] == 0 and fi["market"]["median"] is None


def test_fund_info_parity_with_js(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", REAL_PUBLIC)
    path = REAL_PUBLIC / "rusetfs_funds.json"
    if not path.exists():
        pytest.skip("нет public/data/rusetfs_funds.json")
    rows = json.loads(path.read_text(encoding="utf-8"))["data"]
    tickers = []
    for cls in sorted({r.get("asset_class") for r in rows if r.get("asset_class")}):
        tickers.append(next(r["ticker"] for r in rows if r.get("asset_class") == cls))
    tickers += [next(r["ticker"] for r in rows if r.get("trade_status") not in (None, "Торгуется")),
                "LQDT", "TMOS", "NOSUCH"]
    js = _run_parity({"dataDir": str(REAL_PUBLIC), "funds": tickers}, tmp_path)["funds"]
    for t in tickers:
        py, j = sp.fund_info(t), js[t]
        assert (py["info"] is None) == (j["info"] is None), t
        if py["info"] is not None:
            assert py["info"] == j["info"], t
        pm, jm = py["market"], j["market"]
        for a, b in [("min", "min"), ("p25", "p25"), ("median", "median"), ("p75", "p75"), ("max", "max"),
                     ("n", "n"), ("class_median", "classMedian"), ("market_median", "marketMedian")]:
            if jm[b] is None:
                assert pm[a] is None, (t, a)
            else:
                assert pm[a] == pytest.approx(jm[b], rel=1e-12), (t, a)


# ====================================================================== rating / symbol_stats / load_file
def test_rating_and_symbol_stats(pub):
    r = sp.rating("sber")
    assert r["class"] == "share" and r["score"] == 80.0 and r["n_class"] == 3
    assert r["total_return"] is True and r["updated"].endswith("T03:00:00+00:00")
    r["rank"]["sharpe"] = -1        # копия: кэш symbol_stats не портится
    assert public_data.symbol_stats()["items"]["SBER"]["rank"]["sharpe"] == 80.0
    assert sp.rating("NOSUCH") is None


def test_rating_without_file_raises(no_pub):
    assert public_data.symbol_stats() is None
    with pytest.raises(ValueError):
        sp.rating("SBER")


def test_symbol_stats_broken_or_without_items(no_pub):
    (no_pub / "symbol_stats.json").write_text("{не json", encoding="utf-8")
    assert public_data.symbol_stats() is None and public_data.load_file("symbol_stats") is None
    _write(no_pub, "symbol_stats", {"updated": "x", "items": []})
    assert public_data.symbol_stats() is None
    assert public_data.load_file("symbol_stats") == {"updated": "x", "items": []}
    assert public_data.load_file("missing") is None


def test_load_dividends_mapping(pub):
    d = sp.load_dividends("sber")
    assert [x["record_date"] for x in d] == ["2023-05-11", "2024-07-11", "2025-07-18", "2025-08-01", "2025-09-01"]
    assert d[0]["ex_date"] == "2023-05-11"            # last_buy_date 10.05 (ср) -> 11.05 (чт)
    assert d[2]["ex_date"] == "2025-07-18"            # 17.07 (чт) -> 18.07 (пт)
    assert d[4]["ex_date"] == "2025-09-01"            # 29.08 (пт) -> 01.09 (пн)
    assert d[3]["ex_date"] == "2025-08-01"            # нет last_buy_date -> record_date
    assert d[0]["yield"] == pytest.approx(0.10) and d[3]["yield"] is None
    assert d[3]["currency"] == "USD" and d[4]["cancelled"] is True
    assert d[0]["payment_date"] == "2023-05-20" and d[1]["payment_date"] is None
    assert sp.load_dividends("NOSUCH") == []


# ====================================================================== load_symbol
def test_load_symbol_share(use_iss, pub):
    use_iss()
    d = sp.load_symbol(" sber ")
    assert d["secid"] == "SBER" and d["errors"] == {}
    assert set(d) == {"secid", "info", "ohlc", "snapshot", "dividends", "coupons", "fund", "rating", "errors"}
    info = d["info"]
    assert info["cls"] == "share" and info["benchmark"] == "MCFTR" and info["issuer"] == "ПАО Сбербанк"
    assert info["currency"] == "RUB" and info["list_level"] == 1 and info["board"] == "TQBR"
    assert info["first_trade_date"] == "1997-03-24"     # самая ранняя history_from режимов рынка (EQBR)
    o = d["ohlc"]
    ref = iss_fake.ohlc("SBER")
    assert len(o) == len(ref) and o["close"].iloc[-1] == pytest.approx(ref["close"].iloc[-1])
    s = d["snapshot"]
    assert s["last"] == pytest.approx(round(ref["close"].iloc[-1] * 1.004, 6))
    assert s["prev_close"] == pytest.approx(ref["close"].iloc[-1])
    assert s["change_pct"] == pytest.approx(0.004, rel=1e-4)
    assert s["market_cap"] == pytest.approx(s["last"] * 21586948000)
    assert s["bond"] is None and s["date"] == dt.date.today().isoformat()
    assert len(d["dividends"]) == 5 and d["coupons"] is None and d["fund"] is None
    assert d["rating"]["score"] == 80.0


def test_load_symbol_fund(use_iss, pub):
    use_iss()
    d = sp.load_symbol("LQDT")
    assert d["errors"] == {} and d["info"]["cls"] == "fund" and d["info"]["benchmark"] == "MCFTR"
    assert d["fund"]["info"]["ticker"] == "LQDT" and d["fund"]["market"]["n"] == 3
    assert d["coupons"] is None and d["dividends"] == []
    assert d["rating"]["class"] == "fund"


def test_load_symbol_bond(use_iss, pub):
    use_iss()
    d = sp.load_symbol("SU26238RMFS4")
    assert d["errors"] == {}
    info = d["info"]
    assert info["cls"] == "bond" and info["benchmark"] == "RGBITR"
    assert info["face_value"] == 1000.0 and info["coupon_percent"] == 7.1 and info["coupon_period"] == 182
    assert info["mat_date"] == "2041-05-15" and info["offer_date"] is None
    assert isinstance(d["coupons"], pd.DataFrame) and len(d["coupons"]) > 30
    b = d["snapshot"]["bond"]
    assert b["yield"] == pytest.approx(0.1425) and b["duration"] == pytest.approx(3650 / 365.25)
    assert b["accrued_int"] == 12.5
    assert d["fund"] is None and d["rating"] is None


def test_load_symbol_index(use_iss, pub):
    s = use_iss()
    d = sp.load_symbol("IMOEX")
    assert d["errors"] == {} and d["info"]["cls"] == "index" and d["info"]["benchmark"] == "MCFTR"
    ref = iss_fake.ohlc("IMOEX")
    o = d["ohlc"]
    assert len(o) == len(ref) and o["volume"].isna().all()
    assert o["close"].tolist() == pytest.approx(ref["close"].tolist())
    hist = [_q(u)[1] for u in s.calls if "/history/" in u]
    assert hist and all(q["till"] < iss_fake.INDEX_CANDLES_FROM for q in hist)
    snap = d["snapshot"]
    assert snap["prev_close"] == pytest.approx(ref["close"].iloc[-1], rel=1e-6)
    assert snap["market_cap"] == 6.5e13
    assert d["coupons"] is None and d["fund"] is None


def test_load_symbol_unknown_ticker_no_exception(use_iss, pub):
    use_iss()
    d = sp.load_symbol("NOSUCH")
    assert d["info"] is None and d["ohlc"] is None and d["snapshot"] is None
    assert {"info", "ohlc", "snapshot"} <= set(d["errors"])
    assert all(isinstance(v, str) and v for v in d["errors"].values())
    assert d["dividends"] == [] and d["fund"] is None and d["rating"] is None


def test_load_symbol_iss_down(use_iss, pub):
    use_iss(fail=True)
    d = sp.load_symbol("SBER")
    assert {"info", "ohlc", "snapshot"} <= set(d["errors"])
    assert "недоступен" in d["errors"]["info"]
    assert len(d["dividends"]) == 5 and d["rating"]["score"] == 80.0      # файлы сборщика — независимо


def test_load_symbol_years(use_iss, pub):
    use_iss()
    d = sp.load_symbol("SBER", years=1)
    start = pd.Timestamp.today().normalize() - pd.DateOffset(years=1)
    assert d["ohlc"].index[0] >= start
    assert d["ohlc"].index[0] - start < pd.Timedelta(days=5)
    full = sp.load_symbol("SBER")
    assert len(full["ohlc"]) > 4 * len(d["ohlc"])


def test_load_symbol_without_public_files(use_iss, no_pub):
    use_iss()
    d = sp.load_symbol("SBER")
    assert d["dividends"] == [] and d["rating"] is None
    assert "ночного пересчёта" in d["errors"]["rating"]
    assert set(d["errors"]) == {"rating"}
    f = sp.load_symbol("LQDT")
    assert f["fund"] == {"info": None, "market": {"min": None, "p25": None, "median": None, "p75": None,
                                                  "max": None, "n": 0, "class_median": None, "market_median": None}}


def test_load_symbol_and_report_pickle(use_iss, pub):
    use_iss()
    d = sp.load_symbol("SBER")
    back = pickle.loads(pickle.dumps(d))
    assert back["info"] == d["info"]
    pd.testing.assert_frame_equal(back["ohlc"], d["ohlc"])
    bench = iss_fake.ohlc("MCFTR")["close"]
    rep = sp.report(d, bench, 0.1)
    rb = pickle.loads(pickle.dumps(rep))
    assert rb["metrics"]["security"].sharpe == pytest.approx(rep["metrics"]["security"].sharpe)


# ====================================================================== align_benchmark
def test_align_benchmark_asof():
    dates = pd.DatetimeIndex(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
                                             "2024-01-05"]))
    b = pd.Series([10.0, 12.0, 99.0], index=pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-08"]), name="B")
    out = sp.align_benchmark(b, dates)
    assert [str(d.date()) for d in out.index] == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    assert out.tolist() == [10.0, 10.0, 12.0, 12.0] and out.name == "B"


def test_align_benchmark_cleaning_and_short():
    dates = pd.DatetimeIndex(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    b = pd.Series([0.0, np.nan, 5.0, 6.0],
                  index=pd.to_datetime(["2023-12-30", "2023-12-31", "2024-01-02", "2024-01-02"]))
    out = sp.align_benchmark(b, dates)
    assert out.tolist() == [6.0, 6.0]          # дубль даты — последнее значение; 0 и NaN отброшены
    assert sp.align_benchmark(pd.Series([1.0], index=pd.to_datetime(["2024-01-03"])), dates) is None
    assert sp.align_benchmark(None, dates) is None
    assert sp.align_benchmark(pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-01-02"])),
                              pd.DatetimeIndex([])) is None
    assert sp.align_benchmark(pd.Series(dtype=float), dates) is None


# ====================================================================== report
def _data(close: list, start="2024-01-02", cls="share", divs=None, secid="TST", hl=True):
    idx = pd.bdate_range(start, periods=len(close))
    c = np.array(close, dtype=float)
    o = pd.DataFrame({"open": c, "high": c * (1.01 if hl else 1), "low": c * (0.99 if hl else 1), "close": c,
                      "volume": 1.0}, index=idx)
    return {"secid": secid, "info": {"cls": cls}, "ohlc": o, "dividends": divs or [], "errors": {}}


def test_report_share_total_return_hand_example():
    divs = [{"ex_date": "2024-01-03", "value": 10.0, "currency": "RUB"},
            {"ex_date": "2024-01-04", "value": 50.0, "currency": "RUB", "cancelled": True},
            {"ex_date": "2024-01-04", "value": 50.0, "currency": "USD"}]
    r = sp.report(_data([100.0, 100.0, 100.0], divs=divs), None, 0.0)
    assert r["total_return"] is True
    assert r["series"].tolist() == pytest.approx([100.0, 110.0, 110.0])
    assert r["close"].tolist() == [100.0, 100.0, 100.0]
    assert r["periods"]["security"]["ALL"]["ret"] == pytest.approx(0.10)
    assert r["drawdowns"]["max"] == 0.0
    ds = r["dividend_stats"]
    assert ds["ttm_value"] == 10.0 and ds["ttm_yield"] == pytest.approx(0.1) and ds["as_of"] == "2024-01-04"


def test_report_share_only_cancelled_or_foreign_divs_is_price_return():
    divs = [{"ex_date": "2024-01-03", "value": 10.0, "currency": "USD"},
            {"ex_date": "2024-01-03", "value": 10.0, "cancelled": True}]
    r = sp.report(_data([100.0, 101.0, 102.0], divs=divs), None, 0.0)
    assert r["total_return"] is False and r["series"].tolist() == [100.0, 101.0, 102.0]
    assert r["dividend_stats"]["ttm_value"] == 0.0


def test_report_fund_ignores_dividends():
    divs = [{"ex_date": "2024-01-03", "value": 10.0, "currency": "RUB"}]
    r = sp.report(_data([100.0, 100.0, 100.0], cls="fund", divs=divs), None, 0.0)
    assert r["total_return"] is False and r["dividend_stats"] is None
    assert r["periods"]["security"]["ALL"]["ret"] == 0.0


def _walk(n, seed=1, p0=100.0):
    rng = np.random.default_rng(seed)
    return list(p0 * np.cumprod(1 + rng.normal(0.0005, 0.01, n)))


def test_report_without_benchmark_keys():
    r = sp.report(_data(_walk(400), start="2023-01-02"), None, 0.1)
    assert set(r) == {"series", "close", "benchmark", "periods", "momentum", "monthly", "metrics", "drawdowns",
                      "volatility", "dividend_stats", "total_return", "errors"}
    assert r["errors"] == {}
    assert set(r["periods"]) == {"security", "benchmark"} and r["periods"]["benchmark"] is None
    assert set(r["metrics"]) == {"security", "benchmark"} and r["metrics"]["benchmark"] is None
    assert isinstance(r["metrics"]["security"], m.MetricsReport)
    assert r["benchmark"] is None and r["drawdowns"]["benchmark_series"] is None
    assert r["volatility"]["rolling_benchmark"] is None
    ret = r["periods"]["security"]["ALL"]["ret"]
    assert ret == pytest.approx(r["close"].iloc[-1] / r["close"].iloc[0] - 1)
    v = r["volatility"]
    assert v["to"] == str(r["close"].index[-1].date())
    assert pd.Timestamp(v["from"]) >= r["close"].index[-1] - pd.DateOffset(years=1) - pd.Timedelta(days=4)
    assert len(r["drawdowns"]["top"]) <= 5
    assert r["monthly"]["years"] == [2023, 2024]


def test_report_with_benchmark_aligned():
    d = _data(_walk(300, 1), start="2023-01-02")
    bidx = pd.bdate_range("2022-06-01", periods=500)[::2]           # бенчмарк — через день, раньше и дольше
    bench = pd.Series(_walk(len(bidx), 7, 1000.0), index=bidx, name="MCFTR")
    r = sp.report(d, bench, 0.05)
    b = r["benchmark"]
    assert b.index.equals(r["series"].index)                        # все даты бумаги (бенчмарк начался раньше)
    exp = bench.reindex(bench.index.union(b.index)).ffill().reindex(b.index)
    assert b.tolist() == exp.tolist()
    assert r["periods"]["benchmark"]["ALL"]["ret"] == pytest.approx(b.iloc[-1] / b.iloc[0] - 1)
    sec, bm = r["metrics"]["security"], r["metrics"]["benchmark"]
    assert isinstance(bm, m.MetricsReport) and bm.beta == pytest.approx(1.0)
    ref = m.compute_all(r["series"], b, 0.05, freq="D")
    assert sec.sharpe == pytest.approx(ref.sharpe) and sec.beta == pytest.approx(ref.beta)
    assert isinstance(r["drawdowns"]["benchmark_series"], pd.Series)
    assert isinstance(r["volatility"]["rolling_benchmark"], pd.Series)


def test_report_short_history():
    r = sp.report(_data([100.0, 101.0]), None, 0.0)
    assert r["metrics"] is None and "короткая" in r["errors"]["metrics"]
    assert r["periods"]["security"]["ALL"]["ret"] == pytest.approx(0.01)
    r1 = sp.report(_data([100.0]), None, 0.0)          # одна точка — без исключений
    assert r1["series"].tolist() == [100.0] and r1["metrics"] is None


def test_report_no_ohlc():
    r = sp.report({"secid": "X", "info": None, "ohlc": None, "errors": {"ohlc": "ISS недоступен"}}, None, 0.0)
    assert r["series"] is None and r["errors"] == {"series": "ISS недоступен"}
    r = sp.report({"secid": "X", "info": {"cls": "share"},
                   "ohlc": pd.DataFrame(columns=["open", "high", "low", "close", "volume"])}, None, 0.0)
    assert r["errors"]["series"] == "нет истории торгов" and r["periods"] is None


def test_report_zero_volatility():
    r = sp.report(_data([250.0] * 300, start="2023-01-02", hl=False), None, 0.0)
    assert r["volatility"]["close_to_close"] == 0.0
    assert r["drawdowns"]["max"] == 0.0 and r["drawdowns"]["top"] == []
    assert r["metrics"]["security"].volatility == 0.0


def test_report_drops_nonpositive_closes():
    d = _data([100.0, 0.0, 110.0, 121.0])
    r = sp.report(d, None, 0.0)
    assert r["close"].tolist() == [100.0, 110.0, 121.0]


def test_report_index_ohlc_without_volume():
    d = _data(_walk(300), start="2023-01-02", cls="index")
    d["ohlc"]["volume"] = np.nan
    r = sp.report(d, None, 0.0)
    assert r["errors"] == {} and r["dividend_stats"] is None and r["total_return"] is False


# ====================================================================== Streamlit (AppTest)
st_testing = pytest.importorskip("streamlit.testing.v1")
_BAD = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(nan|NaN|None|null|NaT|inf)(?![A-Za-zА-Яа-яЁё])")


def _texts(at) -> list[str]:
    out = []
    for kind in ("title", "header", "subheader", "markdown", "caption", "text", "info", "warning", "error",
                 "success"):
        out += [str(e.value) for e in getattr(at, kind)]
    for mt in at.metric:
        out += [str(mt.label), str(mt.value), str(mt.proto.delta or "")]
    for el in at.get("progress"):
        out.append(str(getattr(el.proto, "text", "")))
    for df_el in at.dataframe:
        v = df_el.value
        v = getattr(v, "data", v)
        out += [str(c) for c in v.columns]
        out += [str(x) for x in v.select_dtypes(exclude="number").to_numpy().ravel() if x is not None
                and not (isinstance(x, float) and math.isnan(x))]
    return out


def _assert_clean(at):
    assert not at.exception, [e.value for e in at.exception]
    bad = [t for t in _texts(at) if _BAD.search(t)]
    assert not bad, bad


def _app(path, query=None, **state):
    import streamlit as st
    st.cache_data.clear()
    at = st_testing.AppTest.from_file(str(ROOT / path), default_timeout=120)
    for k, v in (query or {}).items():
        at.query_params[k] = v
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


CARD = "pages/2_Карточка_бумаги.py"


@pytest.fixture
def offline(monkeypatch, use_iss):
    monkeypatch.setenv("IP_OFFLINE", "1")
    use_iss(fail=True)          # демо-режим не должен ходить в ISS; если пойдёт — получит сбой, не сеть


@pytest.fixture
def live(monkeypatch, use_iss, pub):
    """Живой режим, но без сети: ISS отключён, load_symbol и load_series подменяются в тесте."""
    monkeypatch.setenv("IP_OFFLINE", "0")
    use_iss(fail=True)
    import ui.common as uc
    bench = iss_fake.ohlc("MCFTR")["close"].rename("MCFTR")
    monkeypatch.setattr(uc, "load_series", lambda key, start, end=None, total_return=False: bench.loc[start:])
    return monkeypatch


def _fake_world_data(secid):
    """Реалистичный результат load_symbol, построенный на синтетике tests/iss_fake.py (вне AppTest)."""
    return pickle.loads(pickle.dumps(_DATA_CACHE[secid]))


_DATA_CACHE: dict = {}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Заранее загруженные данные для SBER/LQDT/SU26238RMFS4/IMOEX (через подменённую сессию ISS)."""
    if not _DATA_CACHE:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(iss, "_cache", _NoCache())
            mp.setattr(iss, "_session", FakeSession(iss_fake.respond))
            d = tmp_path / "pd"
            d.mkdir()
            _write(d, "dividends", {"data": {"SBER": DIVS_SBER}})
            _write(d, "rusetfs_funds", {"data": FUNDS})
            _write(d, "symbol_stats", iss_fake.symbol_stats())
            mp.setattr(config, "PUBLIC_DATA_DIR", d)
            for s in ("SBER", "LQDT", "SU26238RMFS4", "IMOEX"):
                _DATA_CACHE[s] = sp.load_symbol(s)
    return _DATA_CACHE


def test_card_demo_mcftr(offline):
    at = _app(CARD, {"s": "MCFTR"})
    _assert_clean(at)
    assert any("MCFTR" in s.value for s in at.subheader)
    assert at.get("plotly_chart"), "в демо-режиме должен быть график"


def test_card_demo_sber(offline):
    at = _app(CARD, {"s": "SBER"})
    _assert_clean(at)
    assert any("SBER" in s.value for s in at.subheader)


def test_card_demo_bad_ticker(offline):
    at = _app(CARD, {"s": "SB ER!"})
    assert not at.exception
    assert at.error and "не похоже на тикер" in at.error[0].value


@pytest.mark.parametrize("secid, must", [
    ("SBER", ["Ключевые цифры", "Дивиденды", "Просадки", "Волатильность", "Рейтинг риск/доходность",
              "Относительно бенчмарка MCFTR"]),
    ("LQDT", ["Комиссия фонда против рынка", "Ключевые цифры", "Просадки"]),
    ("SU26238RMFS4", ["Купоны", "Облигация", "Ключевые цифры"]),
    ("IMOEX", ["Ключевые цифры", "Волатильность"]),
])
def test_card_live_with_patched_load_symbol(live, world, secid, must):
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))
    at = _app(CARD, {"s": secid})
    _assert_clean(at)
    heads = " | ".join(e.value for e in at.markdown)
    for h in must:
        assert f"#### {h}" in heads, (h, heads)
    assert not at.warning or all("Блок недоступен" not in w.value for w in at.warning), \
        [w.value for w in at.warning]
    assert any(mt.label == "Цена" and mt.value != "—" for mt in at.metric)
    assert at.button, "нет кнопки «Собрать портфель с этой бумагой»"


def test_card_live_share_values(live, world):
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))
    at = _app(CARD, {"s": "SBER"})
    _assert_clean(at)
    ttm = next(mt for mt in at.metric if mt.label == "Выплаты за 12 мес.")
    # выплаты с ex_date за 365 дней до последней свечи; в DIVS_SBER свежие — 2025 г. (34,84 ₽) или ни одной
    assert ttm.value in ("34,84 ₽", "0,00 ₽")
    score = next(mt for mt in at.metric if mt.label == "Итоговый балл")
    assert score.value == "80 из 100"


def test_card_dividend_table_last_day_with_dividend(live, world):
    """«Последний день с дивидендом» — это last_buy_date (17.07.2025), а не ex_date (18.07.2025 — первый день
    без дивиденда, следующий рабочий день после last_buy_date; web/CONTRACT.md)."""
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))
    at = _app(CARD, {"s": "SBER"})
    assert not at.exception
    tables = [getattr(d.value, "data", d.value) for d in at.dataframe]
    t = next(x for x in tables if "Дата отсечки" in x.columns)
    col = "Последний день с дивидендом"
    if col in t.columns:
        row = t[t["Дата отсечки"] == "18.07.2025"].iloc[0]
        assert row[col] == "17.07.2025", f"{col}: {row[col]} (показан ex_date)"


def test_card_live_compare(live, world):
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))
    at = _app(CARD, {"s": "SBER"}, sym_cmp_in="LQDT")
    _assert_clean(at)
    assert any("Рост 100 ₽" in c.value for c in at.caption)
    at2 = _app(CARD, {"s": "SBER"}, sym_cmp_in="SBER")      # сравнение с собой — молча игнорируется
    _assert_clean(at2)


def test_card_search_by_name(live, use_iss):
    use_iss()                                   # синтетический ISS: /securities?q=
    at = _app(CARD, sym_q="Сбербанк")
    assert not at.exception, [e.value for e in at.exception]
    sb = next(s for s in at.selectbox if s.label == "Найдено")
    assert sb.options[0].startswith("SBER")


def test_card_search_by_name_offline(offline):
    at = _app(CARD, sym_q="Сбербанк")
    assert not at.exception
    assert any("работает только с живыми данными" in w.value for w in at.warning)


def test_card_live_load_failure(live):
    def boom(s, years=None):
        raise requests.ConnectionError("Max retries exceeded")
    live.setattr(sp, "load_symbol", boom)
    at = _app(CARD, {"s": "SBER"})
    _assert_clean(at)
    assert at.error and "нет связи с источником" in at.error[0].value


def test_card_live_all_keys_failed(live):
    data = {"secid": "NOSUCH", **{k: None for k in sp.DATA_KEYS}, "dividends": [],
            "errors": {"info": "Инструмент NOSUCH не найден на ISS", "ohlc": "Инструмент NOSUCH не найден на ISS",
                       "snapshot": "Инструмент NOSUCH не найден на ISS"}}
    live.setattr(sp, "load_symbol", lambda s, years=None: data)
    at = _app(CARD, {"s": "NOSUCH"})
    _assert_clean(at)
    assert any("Справка по NOSUCH недоступна" in w.value for w in at.warning)


def test_card_live_partial_failure(live, world):
    """Справка не загрузилась, свечи есть — расчётные блоки строятся, без исключений."""
    def partial(s, years=None):
        d = _fake_world_data("SBER")
        d["info"], d["snapshot"] = None, None
        d["errors"] = {"info": "ISS недоступен", "snapshot": "ISS недоступен"}
        return d
    live.setattr(sp, "load_symbol", partial)
    at = _app(CARD, {"s": "SBER"})
    _assert_clean(at)
    heads = " | ".join(e.value for e in at.markdown)
    assert "#### Просадки" in heads and "#### Ключевые цифры" in heads


def test_card_live_benchmark_failure(live, world):
    import ui.common as uc
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))

    def nobench(*a, **k):
        raise requests.ConnectionError("Max retries exceeded")
    live.setattr(uc, "load_series", nobench)
    at = _app(CARD, {"s": "SBER"})
    _assert_clean(at)
    assert any("Бенчмарк MCFTR" in w.value for w in at.warning)


def test_card_button_prefills_backtest(live, world):
    live.setattr(sp, "load_symbol", lambda s, years=None: _fake_world_data(s))
    at = _app(CARD, {"s": "SBER"})
    btn = next(b for b in at.button if "Собрать портфель" in str(b.label))
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["bt_prefill"] == {"name": "Портфель с SBER", "weights": {"SBER": 1.0}}
    assert at.session_state["custom_portfolios"]["Портфель с SBER"] == {"SBER": 1.0}


def test_backtest_prefill(offline):
    msg = "SBER добавлена в собственные портфели («Портфель с SBER») с долей 100 %."
    at = _app("pages/4_Бэктест.py", bt_prefill={"name": "Портфель с SBER", "weights": {"SBER": 1.0}},
              bt_prefill_msg=msg, custom_portfolios={"Портфель с SBER": {"SBER": 1.0}})
    assert not at.exception, [e.value for e in at.exception]
    assert any(msg in s.value for s in at.success)
    assert at.text_input[0].value == "Портфель с SBER"
    assert at.expander[0].proto.expanded is True
    ed = at.get("arrow_data_frame") or at.dataframe
    # редактор с SBER 100 % и портфель доступен в выборе стратегий
    ms = at.multiselect[0]
    assert "Портфель с SBER" in ms.options and "Портфель с SBER" in ms.value
    assert "bt_prefill_msg" not in at.session_state      # сообщение показывается один раз
    at.run()
    assert not any(msg in s.value for s in at.success)


def test_backtest_without_prefill(offline):
    at = _app("pages/4_Бэктест.py")
    assert not at.exception
    assert at.text_input[0].value == "Мой портфель"


# ---------------------------------------------------------------------- Витрина и Обзор
def _showcase(secids, names):
    return pd.DataFrame({"SECID": secids, "Название": names, "Цена": [1.0] * len(secids),
                         "1Г": [0.1] * len(secids)})


@pytest.fixture
def showcases(monkeypatch):
    from core.analytics import screener
    monkeypatch.setattr(screener, "shares_showcase", lambda b, rf, top: _showcase(["SBER", "GAZP", "ZZZZ"],
                                                                                 ["Сбер", "Газпром", "Нет"]))
    monkeypatch.setattr(screener, "etf_showcase", lambda b, rf: _showcase(["LQDT", "TMOS"], ["Ликв", "Тмос"]))
    monkeypatch.setattr(screener, "bonds_showcase", lambda board: pd.DataFrame(
        {"SECID": ["SU1"], "Название": ["ОФЗ"], "Лет до погаш.": [5.0], "Оборот, руб.": [1e6], "Листинг": [1]}))


def test_showcase_rating_columns(offline, pub, showcases):
    at = _app("pages/1_Витрина.py")
    assert not at.exception, [e.value for e in at.exception]
    sh = at.dataframe[0].value
    cols = list(sh.columns)
    assert cols[cols.index("Название") + 1:cols.index("Название") + 3] == ["Рейтинг", "Шарп, перц."]
    assert sh.set_index("SECID").loc["SBER", "Рейтинг"] == 80 and sh.set_index("SECID").loc["GAZP", "Шарп, перц."] == 20
    assert pd.isna(sh.set_index("SECID").loc["ZZZZ", "Рейтинг"])
    fu = at.dataframe[1].value.set_index("SECID")
    assert fu.loc["LQDT", "Рейтинг"] == 60 and fu.loc["TMOS", "Шарп, перц."] == 40
    assert "Рейтинг" not in at.dataframe[2].value.columns          # облигации — без рейтинга
    assert any("Рейтинг — итоговый балл" in c.value for c in at.caption)


def test_showcase_without_symbol_stats(offline, no_pub, showcases):
    at = _app("pages/1_Витрина.py")
    assert not at.exception
    assert "Рейтинг" not in at.dataframe[0].value.columns
    assert not any("Рейтинг — итоговый балл" in c.value for c in at.caption)


def _overview_text(at) -> str:
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [str(getattr(el.proto, "label", "")) for el in at.get("page_link")]
    return "\n".join(parts)


def test_overview_leaders(offline, pub):
    at = _app("pages/0_Обзор.py")
    assert not at.exception, [e.value for e in at.exception]
    txt = _overview_text(at)
    assert "Лидеры рейтинга" in " ".join(s.value for s in at.subheader)
    for line in ["1. LKOH — 100 из 100", "2. SBER — 80 из 100", "3. GAZP — 20 из 100",
                 "1. LQDT — 60 из 100", "2. TMOS — 40 из 100"]:
        assert line in txt, (line, txt)
    assert txt.index("1. LKOH") < txt.index("2. SBER") < txt.index("3. GAZP")
    assert not at.error, [e.value for e in at.error]


def test_overview_without_symbol_stats(offline, no_pub):
    at = _app("pages/0_Обзор.py")
    assert not at.exception
    assert any("Рейтинг появится после ночного пересчёта" in c.value for c in at.caption)
