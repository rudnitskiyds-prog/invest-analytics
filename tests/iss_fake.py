"""Поддельный ISS Мосбиржи для смоук-тестов веб-версии (tests/test_web_smoke.py): ответ по URL.

Только синтетика (детерминированные ряды по crc32 тикера) — реальные котировки в репозиторий не кладём
(инвариант 5 CLAUDE.md). Формат блоков — как у ISS: {"columns": [...], "data": [[...], ...]}.
"""
from __future__ import annotations

import datetime as dt
import re
import zlib
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
import pandas as pd

TODAY = dt.date.today()
LAST_DAY = pd.Timestamp(TODAY) - pd.tseries.offsets.BDay(1)        # последняя торговая дата истории

# secid -> описание бумаги
SECURITIES = {
    "SBER": {"cls": "share", "engine": "stock", "market": "shares", "board": "TQBR", "start": "2014-01-06",
             "p0": 100.0, "mu": 0.0004, "sigma": 0.018, "name": "Сбербанк России ПАО ао", "short": "Сбербанк",
             "isin": "RU0009029540", "type": "common_share", "group": "stock_shares", "typename": "Акция обыкновенная",
             "sectype": "1", "issuer": "ПАО Сбербанк", "issuesize": 21586948000, "hist": [("EQBR", "1997-03-24")]},
    "LQDT": {"cls": "fund", "engine": "stock", "market": "shares", "board": "TQBR", "start": "2020-01-20",
             "p0": 1.0, "mu": 0.0005, "sigma": 0.0004, "name": "БПИФ «Ликвидность»", "short": "LQDT",
             "isin": "RU000A1014L8", "type": "exchange_ppif", "group": "stock_ppif", "typename": "Пай биржевого ПИФа",
             "sectype": "J", "issuer": "ВИМ Инвестиции"},
    "SU26238RMFS4": {"cls": "bond", "engine": "stock", "market": "bonds", "board": "TQOB", "start": "2021-06-16",
                     "p0": 98.0, "mu": -0.0002, "sigma": 0.006, "name": "ОФЗ-ПД 26238 15/05/2041", "short": "ОФЗ 26238",
                     "isin": "RU000A1038V6", "type": "ofz_bond", "group": "stock_bonds", "typename": "ОФЗ",
                     "sectype": "3", "issuer": "Министерство финансов РФ"},
    "IMOEX": {"cls": "index", "engine": "stock", "market": "index", "board": "SNDX", "start": "2013-01-08",
              "p0": 1500.0, "mu": 0.0003, "sigma": 0.013, "name": "Индекс МосБиржи", "short": "IMOEX", "isin": None,
              "type": "stock_index", "group": "stock_index", "typename": "Индекс фондового рынка", "sectype": None},
}
INDEX_ONLY = {"MCFTR": (3000.0, 0.0005), "RGBITR": (400.0, 0.0003), "RUCBTRNS": (150.0, 0.0003),
              "MEBCTR": (2000.0, 0.0004), "IRDIVTR": (5000.0, 0.0005), "RUCBITR": (300.0, 0.0003)}
for _k, (_p0, _mu) in INDEX_ONLY.items():
    SECURITIES[_k] = {"cls": "index", "engine": "stock", "market": "index", "board": "SNDX", "start": "2013-01-08",
                      "p0": _p0, "mu": _mu, "sigma": 0.01, "name": f"Индекс {_k}", "short": _k, "isin": None,
                      "type": "stock_index", "group": "stock_index", "typename": "Индекс", "sectype": None}

PAGE_CANDLES = 500
INDEX_CANDLES_FROM = "2018-01-03"     # с этой даты у индексов есть дневные свечи (candleborders)
PAGE_HISTORY = 100


def block(columns, rows):
    return {"columns": list(columns), "data": [list(r) for r in rows]}


@lru_cache(maxsize=None)
def ohlc(secid: str) -> pd.DataFrame:
    """Дневные OHLC + объём: случайное блуждание, сид — crc32 тикера."""
    s = SECURITIES[secid]
    idx = pd.bdate_range(s["start"], LAST_DAY)
    rng = np.random.default_rng(zlib.crc32(secid.encode()))
    c = s["p0"] * np.cumprod(1 + rng.normal(s["mu"], s["sigma"], len(idx)))
    o = np.r_[s["p0"], c[:-1]] * (1 + rng.normal(0, s["sigma"] / 4, len(idx)))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, s["sigma"] / 3, len(idx))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, s["sigma"] / 3, len(idx))))
    v = rng.integers(1_000, 1_000_000, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx).round(6)


def _window(df: pd.DataFrame, q: dict) -> pd.DataFrame:
    f, t = q.get("from"), q.get("till")
    if f:
        df = df[df.index >= pd.Timestamp(f)]
    if t:
        df = df[df.index <= pd.Timestamp(t)]
    return df


def _description(secid: str) -> dict:
    s = SECURITIES[secid]
    pairs = [("SECID", secid), ("NAME", s["name"]), ("SHORTNAME", s["short"]), ("ISIN", s["isin"]),
             ("TYPE", s["type"]), ("GROUP", s["group"]), ("TYPENAME", s["typename"])]
    if s["cls"] in ("share", "fund"):
        pairs += [("LISTLEVEL", "1"), ("FACEUNIT", "SUR"), ("ISSUEDATE", "2007-07-11")]
    if s["cls"] == "bond":
        pairs += [("FACEVALUE", "1000"), ("FACEUNIT", "SUR"), ("ISSUEDATE", "2021-06-16"), ("MATDATE", "2041-05-15"),
                  ("COUPONFREQUENCY", "2"), ("COUPONPERCENT", "7.1"), ("LISTLEVEL", "1")]
    return {"description": block(["name", "title", "value"], [(k, k, v) for k, v in pairs if v is not None])}


def _boards(secid: str) -> dict:
    s = SECURITIES[secid]
    rows = [(secid, s["board"], s["market"], s["engine"], 1, s["start"], LAST_DAY.date().isoformat())]
    for b, start in s.get("hist", []):
        rows.append((secid, b, s["market"], s["engine"], 0, start, "2013-03-22"))
    return {"boards": block(["secid", "boardid", "market", "engine", "is_primary", "history_from", "history_till"], rows)}


def _search(q: dict) -> dict:
    text = (q.get("q") or "").lower()
    cols = ["secid", "shortname", "name", "isin", "type", "group", "primary_boardid", "is_traded", "emitent_title"]
    rows = []
    for k, s in SECURITIES.items():
        if text and (text in k.lower() or text in s["name"].lower() or text in (s["short"] or "").lower()):
            rows.append((k, s["short"], s["name"], s["isin"], s["type"], s["group"], s["board"], 1, s.get("issuer")))
    return {"securities": block(cols, rows)}


def _snapshot(secid: str) -> dict:
    s = SECURITIES[secid]
    df = ohlc(secid)
    last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
    now = round(last * 1.004, 6)
    if s["cls"] == "index":
        return {"marketdata": block(["SECID", "CURRENTVALUE", "LASTCHANGE", "TRADEDATE", "CAPITALIZATION"],
                                    [(secid, now, round(now - last, 6), TODAY.isoformat(), 6.5e13)])}
    sec_cols = ["SECID", "PREVPRICE", "ISSUESIZE", "SECTYPE", "PREVDATE"]
    sec_row = [secid, last, s.get("issuesize"), s["sectype"], df.index[-1].date().isoformat()]
    md_cols = ["SECID", "LAST", "VALTODAY_RUR", "ISSUECAPITALIZATION", "TRADEDATE", "UPDATETIME"]
    md_row = [secid, now, 12_345_678_901.0, (now * s["issuesize"]) if s.get("issuesize") else None,
              TODAY.isoformat(), "18:39:59"]
    out = {}
    if s["cls"] == "bond":
        sec_cols += ["ACCRUEDINT", "COUPONVALUE", "NEXTCOUPON", "FACEVALUE"]
        sec_row += [12.5, 35.4, (TODAY + dt.timedelta(days=40)).isoformat(), 1000]
        md_cols += ["YIELD", "DURATION"]
        md_row += [14.25, 3650]
        out["marketdata_yields"] = block(["SECID", "EFFECTIVEYIELD", "DURATION"], [(secid, 14.6, 3650)])
    out["securities"] = block(sec_cols, [sec_row])
    out["marketdata"] = block(md_cols, [md_row])
    return out


def _candles(secid: str, q: dict) -> dict:
    df = _window(ohlc(secid), q)
    start = int(q.get("start") or 0)
    part = df.iloc[start:start + PAGE_CANDLES]
    rows = [(r.open, r.close, r.high, r.low, round(r.close * r.volume, 2), int(r.volume),
             f"{d.date()} 00:00:00", f"{d.date()} 23:59:59") for d, r in part.iterrows()]
    return {"candles": block(["open", "close", "high", "low", "value", "volume", "begin", "end"], rows)}


def _history(secid: str, q: dict) -> dict:
    df = _window(ohlc(secid), q)
    start = int(q.get("start") or 0)
    part = df.iloc[start:start + PAGE_HISTORY]
    rows = [(secid, d.date().isoformat(), r.open, r.high, r.low, r.close) for d, r in part.iterrows()]
    return {"history": block(["SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE"], rows),
            "history.cursor": block(["INDEX", "TOTAL", "PAGESIZE"], [(start, len(df), PAGE_HISTORY)])}


def _bondization(secid: str, q: dict) -> dict:
    dates = pd.date_range("2021-11-17", "2041-05-15", freq="182D")
    rows = [(secid, d.date().isoformat(), 35.4, 35.4, 7.1) for d in dates]
    start = int(q.get("start") or 0)
    lim = int(q.get("limit") or 100)
    return {"coupons": block(["secid", "coupondate", "value", "value_rub", "valueprc"], rows[start:start + lim]),
            "coupons.cursor": block(["INDEX", "TOTAL", "PAGESIZE"], [(start, len(rows), lim)]),
            "amortizations": block(["secid", "amortdate", "value", "value_rub"],
                                   [(secid, "2041-05-15", 1000, 1000)][start:start + lim]),
            "amortizations.cursor": block(["INDEX", "TOTAL", "PAGESIZE"], [(start, 1, lim)]),
            "offers": block(["secid", "offerdate", "offertype"], [])}


def respond(url: str):
    """(status, body | None) для URL iss.moex.com; None — маршрут не знаком (тест должен упасть)."""
    u = urlparse(url)
    path = unquote(u.path)
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    m = re.fullmatch(r"/iss/securities\.json", path)
    if m:
        return 200, _search(q)
    m = re.fullmatch(r"/iss/securities/([^/]+)\.json", path)
    if m:
        sid = m.group(1).upper()
        if sid not in SECURITIES:     # ISS отвечает 200 с пустыми блоками
            return 200, {"description": block(["name", "title", "value"], []),
                         "boards": block(["secid", "boardid", "market", "engine", "is_primary"], [])}
        return 200, {**_description(sid), **_boards(sid)}
    m = re.fullmatch(r"/iss/engines/[^/]+/markets/[^/]+/boards/[^/]+/securities/([^/]+)/candles\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, _candles(m.group(1), q)
    # индексы: дневные свечи рынка index (без режима) и граница их начала
    m = re.fullmatch(r"/iss/engines/stock/markets/index/securities/([^/]+)/candles\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, _candles(m.group(1), q)
    m = re.fullmatch(r"/iss/engines/stock/markets/index/securities/([^/]+)/candleborders\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, {"borders": block(["begin", "end", "interval", "board_group_id"], [
            (f"{INDEX_CANDLES_FROM} 00:00:00", f"{LAST_DAY.date()} 18:50:00", 24, 9),
            ("2011-12-01 00:00:00", f"{LAST_DAY.date()} 18:50:00", 31, 9)])}
    m = re.fullmatch(r"/iss/engines/[^/]+/markets/[^/]+/boards/[^/]+/securities/([^/]+)\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, _snapshot(m.group(1))
    m = re.fullmatch(r"/iss/history/engines/stock/markets/index/securities/([^/]+)\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, _history(m.group(1), q)
    m = re.fullmatch(r"/iss/statistics/engines/stock/markets/bonds/bondization/([^/]+)\.json", path)
    if m and m.group(1) in SECURITIES:
        return 200, _bondization(m.group(1), q)
    return None


def symbol_stats() -> dict:
    """Синтетический public/data/symbol_stats.json (формат контракта) — для рейтинга и лидеров на главной."""
    def item(cls, k):
        base = {"sharpe": 0.5 + k / 10, "sortino": 0.8 + k / 10, "omega": 1.2 + k / 20, "calmar": 0.3 + k / 20,
                "martin": 1.0 + k / 5, "cagr": 0.1 + k / 100, "volatility": 0.25, "max_drawdown": -0.4, "months": 120}
        return {"class": cls, "tr": cls == "share", **base, "rank": {r: 20.0 * k for r in ("sharpe", "sortino", "omega", "calmar", "martin")},
                "score": 20.0 * k}
    items = {"SBER": item("share", 4), "GAZP": item("share", 1), "LKOH": item("share", 5),
             "LQDT": item("fund", 3), "TMOS": item("fund", 2)}
    return {"updated": f"{TODAY.isoformat()}T03:00:00+00:00", "source": "ISS MOEX (расчёт ИнвестАналитики)",
            "total_return": True, "rf": 0.16,
            "window": {"from": "2016-09-30", "till": "2026-09-30", "max_months": 120, "min_months": 36},
            "classes": {"share": {"n": 3}, "fund": {"n": 2}}, "items": items}
