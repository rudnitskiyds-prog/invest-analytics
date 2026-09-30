"""Разбор ответов ISS и Банка России на образцах реальных ответов (без сети)."""
import pandas as pd
import pytest

from core.data import cbr, iss, universe

METAL_XML = ('<?xml version="1.0" encoding="windows-1251"?><Metall FromDate="20110110" ToDate="20110120" '
             'name="Precious metals quotations"><Record Date="12.01.2011" Code="1"><Buy>1359,76</Buy>'
             '<Sell>1359,76</Sell></Record><Record Date="12.01.2011" Code="2"><Buy>28,35</Buy><Sell>28,35</Sell>'
             '</Record><Record Date="13.01.2011" Code="1"><Buy>1352,15</Buy><Sell>1352,15</Sell></Record></Metall>')
RUONIA_XML = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
              '<soap:Body><RuoniaXMLResponse xmlns="http://web.cbr.ru/"><RuoniaXMLResult><Ruonia xmlns="">'
              '<ro><D0>2011-01-11T00:00:00+03:00</D0><ruo>2.4400</ruo><vol>43.98</vol></ro>'
              '<ro><D0>2011-01-12T00:00:00+03:00</D0><ruo>2.5300</ruo><vol>30.57</vol></ro>'
              '<ro><D0>2011-01-14T00:00:00+03:00</D0><ruo>2.5000</ruo><vol>21.38</vol></ro>'
              '</Ruonia></RuoniaXMLResult></RuoniaXMLResponse></soap:Body></soap:Envelope>')


def test_cbr_gold(monkeypatch):
    monkeypatch.setattr(cbr, "_fetch", lambda *a, **k: METAL_XML)
    s = cbr.gold_price("2011-01-10", "2011-01-20")
    assert list(s.values) == [1359.76, 1352.15]
    assert s.index[0] == pd.Timestamp("2011-01-12")


def test_cbr_ruonia_index(monkeypatch):
    monkeypatch.setattr(cbr, "_fetch", lambda *a, **k: RUONIA_XML)
    idx = cbr.ruonia_index("2011-01-10", "2011-01-20")
    assert idx.iloc[0] == 100
    assert idx.iloc[1] == pytest.approx(100 * (1 + 0.0244 / 365))
    # 12.01 -> 14.01: два дня по ставке 12.01
    assert idx.iloc[2] == pytest.approx(idx.iloc[1] * (1 + 0.0253 * 2 / 365))


def _blk(cols, rows):
    return {"columns": cols, "data": rows}


BOARDS_COLS = ["secid", "boardid", "title", "board_group_id", "market_id", "market", "engine_id", "engine",
               "is_traded", "decimals", "history_from", "history_till", "listed_from", "listed_till",
               "is_primary", "currencyid", "unit"]


def fake_iss(path, params=None, ttl=None, retries=3):
    params = params or {}
    if path == "/securities/SBER":
        return {"description": _blk(["name", "title", "value"], [["NAME", "", "Сбербанк"], ["ISIN", "", "RU0009029540"]]),
                "boards": _blk(BOARDS_COLS, [
                    ["SBER", "TQBR", "T+", 57, 1, "shares", 1, "stock", 1, 2, "2013-03-25", "2026-09-28", "", "", 1, "RUB", "M"],
                    ["SBER", "EQBR", "А1", 6, 1, "shares", 1, "stock", 0, 2, "2011-11-21", "2013-08-30", "", "", 0, "RUB", "M"],
                    ["SBER", "SMAL", "Неполные лоты", 9, 1, "shares", 1, "stock", 1, 2, "2011-11-21", "2026-09-28", "", "", 0, "RUB", "M"]])}
    if path.endswith("/boards/EQBR/securities/SBER/candles"):
        return {"candles": _blk(["open", "close", "high", "low", "value", "volume", "begin", "end"],
                                [[1, 90.0, 1, 1, 0, 0, "2013-03-21 00:00:00", ""], [1, 91.0, 1, 1, 0, 0, "2013-03-22 00:00:00", ""]])}
    if path.endswith("/boards/TQBR/securities/SBER/candles"):
        if params.get("start", 0) == 0:
            rows = [[1, 100.0 + i, 1, 1, 0, 0, str((pd.Timestamp("2013-03-25") + pd.Timedelta(days=i)).date()) + " 00:00:00", ""]
                    for i in range(500)]
        else:
            rows = [[1, 700.0, 1, 1, 0, 0, "2014-08-08 00:00:00", ""]]
        return {"candles": _blk(["open", "close", "high", "low", "value", "volume", "begin", "end"], rows)}
    if path == "/securities/MCFTR":
        return {"description": _blk(["name", "title", "value"], []),
                "boards": _blk(BOARDS_COLS, [["MCFTR", "RTSI", "Индексы", 9, 5, "index", 1, "stock", 1, 4,
                                              "2003-02-26", "2026-09-28", "", "", 1, None, "P"]])}
    if path.startswith("/history/engines/stock/markets/index/securities/MCFTR"):
        st = params.get("start", 0)
        rows = [["2011-01-11", 1895.07], ["2011-01-12", 1945.37]] if st == 0 else [["2011-06-06", 1806.78]]
        return {"history": _blk(["TRADEDATE", "CLOSE"], rows),
                "history.cursor": _blk(["INDEX", "TOTAL", "PAGESIZE"], [[st, 3, 2]])}
    raise AssertionError(path)


def test_iss_resolve_and_stitch(monkeypatch):
    monkeypatch.setattr(iss, "get_json", fake_iss)
    iss.resolve.cache_clear()
    info = iss.resolve("SBER")
    assert info["board"] == "TQBR" and info["market"] == "shares"
    assert [b[0] for b in info["boards_hist"]] == ["EQBR"]       # SMAL (неполные лоты) не склеиваем
    s = iss.close_series("SBER", "2011-01-01", "2026-01-01")
    assert s.index[0] == pd.Timestamp("2013-03-21")             # история EQBR подклеена
    assert len(s) == 2 + 500 + 1                                # пагинация свечей по 500
    assert s.index.is_monotonic_increasing


def test_iss_index_history_paging(monkeypatch):
    monkeypatch.setattr(iss, "get_json", fake_iss)
    iss.resolve.cache_clear()
    s = iss.close_series("MCFTR", "2011-01-01", "2011-12-31")
    assert list(s.values) == [1895.07, 1945.37, 1806.78]


def test_chain():
    idx = pd.date_range("2018-12-25", periods=6, freq="D")
    old = pd.Series([100, 101, 102, 103, 104, 105.0], index=idx)
    new = pd.Series([None, None, None, 10, 10.5, 11.0], index=idx)
    ch = universe.chain(old, new, "2018-12-28")
    assert ch.loc["2018-12-28"] == 103
    assert ch.iloc[-1] == pytest.approx(103 * 1.1)
