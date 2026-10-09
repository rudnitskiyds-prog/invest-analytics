"""
Клиент ISS Московской биржи (https://iss.moex.com/iss/reference/).

Публичный API без ключа, данные с задержкой 15 мин (реального времени —
по подписке). Все ответы кэшируются в SQLite (core/data/cache.py).

Что умеет:
  * resolve(secid)              — движок / рынок / основной режим бумаги;
  * candles(secid, …)           — свечи (день/неделя/месяц) с пагинацией;
  * monthly_closes(secid, …)    — месячные цены закрытия режима TQBR одним запросом (рейтинги);
  * splits(secid)               — сплиты и консолидации (statistics/engines/stock/splits);
  * close_series(secid, …)      — ряд цен закрытия для любой бумаги/индекса/металла;
  * shares() / etfs() / bonds() / metals() — витрины инструментов с котировками;
  * dividends(secid), coupons(secid);
  * description / issuer / board_snapshot / daily_ohlc — данные страницы бумаги (core/analytics/symbol_page.py);
  * zcyc(date) / kbd_rate(date, years) — кривая бескупонной доходности (КБД);
  * index_constituents('IMOEX') — состав индекса и веса;
  * search(query).
"""
from __future__ import annotations

import datetime as dt
import time
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd
import requests

from core.config import ISS_BASE, TTL_HISTORY_OPEN, TTL_MARKET, TTL_REFERENCE
from core.data.cache import HttpCache, dumps

_session = requests.Session()
_session.headers.update({"User-Agent": "invest-platform/0.1 (research MVP)"})
_cache = HttpCache()


class ISSError(RuntimeError):
    pass


# --------------------------------------------------------------------- low level
def get_json(path: str, params: Optional[dict] = None, ttl: Optional[float] = TTL_MARKET,
             retries: int = 3) -> dict:
    """GET {ISS_BASE}{path}.json -> dict блоков {name: DataFrame-compatible dict}."""
    url = f"{ISS_BASE}{path}.json"
    params = {"iss.meta": "off", **(params or {})}
    k = HttpCache.key(url, params)
    cached = _cache.get(k, ttl)
    if cached is not None:
        import json
        return json.loads(cached)
    last_err = None
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            _cache.put(k, dumps(data))
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise ISSError(f"ISS недоступен: {url} {params} ({last_err})")


def block(data: dict, name: str) -> pd.DataFrame:
    b = data.get(name)
    if not b:
        return pd.DataFrame()
    return pd.DataFrame(b["data"], columns=b["columns"])


def _paged(path: str, params: dict, name: str, ttl, page_param="start") -> pd.DataFrame:
    frames, start = [], 0
    while True:
        data = get_json(path, {**params, page_param: start}, ttl=ttl)
        df = block(data, name)
        if df.empty:
            break
        frames.append(df)
        cursor = block(data, f"{name}.cursor")
        if not cursor.empty:
            idx, total, size = cursor.iloc[0][["INDEX", "TOTAL", "PAGESIZE"]]
            if idx + size >= total:
                break
            start = idx + size
        else:
            # свечи отдаются страницами по 500 строк без cursor-блока
            if len(df) < 500:
                break
            start += len(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _ttl_for(till: Optional[str]) -> Optional[float]:
    """Исторический диапазон в прошлом — кэш бессрочно."""
    if till and pd.Timestamp(till) < pd.Timestamp.today().normalize() - pd.Timedelta(days=1):
        return None
    return TTL_HISTORY_OPEN


# --------------------------------------------------------------------- reference
# Основные режимы торгов до перехода на Т+ (2013–2014): акции, паи, облигации
LEGACY_MAIN_BOARDS = {"EQBR", "EQNE", "EQBS", "EQNL", "EQLV", "TQBS", "TQNE", "TQNL", "TQLV",
                      "EQTF", "EQOB", "EQOS", "EQDB", "EQNO", "EQQI", "EQIR"}


@lru_cache(maxsize=2048)
def resolve(secid: str) -> dict:
    """Определяет engine / market / board для бумаги (основной режим торгов)."""
    data = get_json(f"/securities/{secid}", ttl=TTL_REFERENCE)
    desc = block(data, "description")
    boards = block(data, "boards")
    if boards.empty:
        raise ISSError(f"Инструмент {secid} не найден на ISS")
    prim = boards[boards["is_primary"] == 1]
    row = (prim if not prim.empty else boards).iloc[0]
    same = boards[(boards["engine"] == row["engine"]) & (boards["market"] == row["market"])]
    info = {"secid": secid, "engine": row["engine"], "market": row["market"],
            "board": row["boardid"], "title": row.get("title", ""),
            "history_from": row.get("history_from"),
            # исторические режимы того же рынка (напр. EQBR до перехода акций в TQBR в 2013 г.)
            "boards_hist": [(r["boardid"], r["history_from"], r["history_till"])
                            for _, r in same.sort_values("history_from").iterrows()
                            if r["history_from"] and r["boardid"] != row["boardid"]
                            and (r["boardid"] in LEGACY_MAIN_BOARDS
                                 or r["board_group_id"] == row["board_group_id"])]}
    if not desc.empty:
        d = dict(zip(desc["name"], desc["value"]))
        info.update({"name": d.get("NAME") or d.get("SHORTNAME"),
                     "shortname": d.get("SHORTNAME"), "isin": d.get("ISIN"),
                     "type": d.get("TYPE"), "group": d.get("GROUP"),
                     "issuedate": d.get("ISSUEDATE"), "facevalue": d.get("FACEVALUE"),
                     "matdate": d.get("MATDATE")})
    return info


def search(query: str, limit: int = 20) -> pd.DataFrame:
    data = get_json("/securities", {"q": query, "limit": limit}, ttl=TTL_REFERENCE)
    df = block(data, "securities")
    cols = [c for c in ["secid", "shortname", "name", "isin", "type", "group",
                        "primary_boardid", "is_traded"] if c in df.columns]
    return df[cols]


# --------------------------------------------------------------------- prices
INTERVAL = {"D": 24, "W": 7, "M": 31, "Q": 4}


def candles(secid: str, start: str = "2000-01-01", end: Optional[str] = None,
            interval: str = "D", engine: Optional[str] = None,
            market: Optional[str] = None, board: Optional[str] = None) -> pd.DataFrame:
    if engine is None or market is None:
        info = resolve(secid)
        engine, market, board = info["engine"], info["market"], board or info["board"]
    end = end or dt.date.today().isoformat()
    path = (f"/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}/candles"
            if board and market != "index" else
            f"/engines/{engine}/markets/{market}/securities/{secid}/candles")
    df = _paged(path, {"from": start, "till": end, "interval": INTERVAL[interval]},
                "candles", ttl=_ttl_for(end))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["begin"]).dt.normalize()
    return df.set_index("date")[["open", "high", "low", "close", "value", "volume"]]


def _splits_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"tradedate", "secid", "before", "after"} <= set(df.columns):
        return pd.DataFrame(columns=["date", "secid", "before", "after"])
    out = pd.DataFrame({"date": pd.to_datetime(df["tradedate"], errors="coerce"), "secid": df["secid"].astype(str),
                        "before": pd.to_numeric(df["before"], errors="coerce"),
                        "after": pd.to_numeric(df["after"], errors="coerce")})
    out = out.dropna()
    out = out[(out["before"] > 0) & (out["after"] > 0)]
    return out.drop_duplicates().sort_values(["secid", "date"]).reset_index(drop=True)


@lru_cache(maxsize=1)
def _all_splits() -> pd.DataFrame:
    """Все сплиты/консолидации фондового рынка: /statistics/engines/stock/splits (блок splits:
    tradedate, secid, before, after). Пагинация — по splits.cursor, если он есть, иначе по start, пока
    страница приносит новые строки."""
    frames, start, seen = [], 0, 0
    for _ in range(100):
        data = get_json("/statistics/engines/stock/splits", {"start": start}, ttl=TTL_REFERENCE)
        df = block(data, "splits")
        if df.empty:
            break
        frames.append(df)
        cur = block(data, "splits.cursor")
        if not cur.empty:
            idx, total, size = (int(cur.iloc[0][k]) for k in ("INDEX", "TOTAL", "PAGESIZE"))
            if idx + size >= total:
                break
            start = idx + size
            continue
        n_new = len(pd.concat(frames).drop_duplicates())
        if n_new == seen or len(df) < 100:
            break
        seen, start = n_new, start + len(df)
    return _splits_frame(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())


def splits(secid: Optional[str] = None) -> pd.DataFrame:
    """Сплиты и консолидации [date, secid, before, after] (сплит 1:100 — before=1, after=100; date — первый день
    торгов в новых акциях). Для рядов из /history (не скорректированы); свечи ISS уже скорректированы биржей.
    secid=None — все бумаги (общий список); иначе — строки бумаги из общего списка, при его недоступности —
    /statistics/engines/stock/splits/{secid}. Оба недоступны — ISSError."""
    try:
        df = _all_splits()
    except ISSError:
        if secid is None:
            raise
        df = _splits_frame(block(get_json(f"/statistics/engines/stock/splits/{secid}", ttl=TTL_REFERENCE), "splits"))
        return df[df["secid"] == secid].reset_index(drop=True)
    return df if secid is None else df[df["secid"] == secid].reset_index(drop=True)


def monthly_closes(secid: str, start: str, end: Optional[str] = None, board: str = "TQBR",
                   engine: str = "stock", market: str = "shares", legacy_boards: tuple = (),
                   adjust_splits: bool = False) -> pd.Series:
    """Месячные цены закрытия режима `board` — свечи interval=31, без resolve() (один запрос на режим
    при окне до 500 месяцев). legacy_boards — прежние режимы того же рынка, склеиваются перед основным
    (фонды: ("TQTF",) — до июня 2026 г. торговались в TQTF); месяц, который есть в обоих, берётся из основного.
    Индекс — последний календарный день месяца свечи; цены ≤ 0 отброшены.
    Свечи ISS (interval 24 и 31; TQBR, EQBR, TQTF) уже скорректированы биржей на сплиты задним числом
    (проверено 08.10.2026: GMKN 03.2024 ≈ 151 при сплите 1:100 от 08.04.2024), поэтому по умолчанию
    adjust_splits=False и s.attrs["split_adjusted"] = True. adjust_splits=True дополнительно применяет
    splits(secid) — для свечей это двойная корректировка (оставлено для рядов из /history и отладки).
    Используется ночным расчётом рейтингов (scripts/collect_data.py, источник symbol_stats)."""
    parts = []
    for b in [*legacy_boards, board]:
        c = candles(secid, start, end, "M", engine, market, b)
        if not c.empty:
            s = c["close"].astype(float)
            s.index = s.index.to_period("M").to_timestamp("M")
            parts.append(s)
    if not parts:                            # нет свечей за окно — пустой ряд с индексом дат (не RangeIndex)
        s = pd.Series(dtype=float, name=secid, index=pd.DatetimeIndex([]))
        s.attrs["split_adjusted"] = True
        return s
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s > 0]
    if adjust_splits:
        from core.analytics.metrics import adjust_splits as _adjust
        try:
            s = _adjust(s, splits(secid))
        except (ISSError, requests.RequestException):
            pass
    s.name = secid
    s.attrs["split_adjusted"] = True        # свечи ISS скорректированы биржей
    return s


def history(secid: str, start: str = "2000-01-01", end: Optional[str] = None,
            engine: Optional[str] = None, market: Optional[str] = None,
            board: Optional[str] = None) -> pd.DataFrame:
    """Итоги торгов (history) — для облигаций даёт доходность, НКД, дюрацию."""
    if engine is None:
        info = resolve(secid)
        engine, market, board = info["engine"], info["market"], board or info["board"]
    end = end or dt.date.today().isoformat()
    path = (f"/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}"
            if market != "index" else f"/history/engines/{engine}/markets/{market}/securities/{secid}")
    df = _paged(path, {"from": start, "till": end}, "history", ttl=_ttl_for(end))
    if df.empty:
        return df
    df["TRADEDATE"] = pd.to_datetime(df["TRADEDATE"])
    return df.set_index("TRADEDATE").sort_index()


def index_history(secid: str, start: str = "2000-01-01", end: Optional[str] = None) -> pd.Series:
    """Индексы: свечи ISS по многим индексам начинаются лишь с 2016 г., история — полная."""
    end = end or dt.date.today().isoformat()
    df = _paged(f"/history/engines/stock/markets/index/securities/{secid}",
                {"from": start, "till": end, "history.columns": "TRADEDATE,CLOSE"},
                "history", ttl=_ttl_for(end))
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["CLOSE"].values, index=pd.to_datetime(df["TRADEDATE"]), dtype=float).dropna()


def close_series(secid: str, start: str = "2000-01-01", end: Optional[str] = None,
                 interval: str = "D", adjust_splits: bool = False) -> pd.Series:
    """Ряд цен закрытия. Для индексов — значение индекса; для облигаций — % от номинала.
    Для бумаг с историей в нескольких режимах (EQBR → TQBR) ряды склеиваются.
    adjust_splits=True — корректировка на сплиты (по умолчанию выключена: прежнее поведение приложения).
    Свечи ISS уже скорректированы биржей — для акций и фондов включать не нужно (двойная корректировка);
    индексы (history) сплитов не имеют."""
    info = resolve(secid)
    if info["market"] == "index":
        s = index_history(secid, start, end)
    else:
        parts = []
        prim_from = info.get("history_from")
        for board, h_from, h_till in info.get("boards_hist", []):
            if prim_from and h_from < prim_from and h_till >= start and (not end or h_from <= end):
                c = candles(secid, max(start, h_from), min(end or h_till, h_till, prim_from),
                            interval, info["engine"], info["market"], board)
                if not c.empty:
                    parts.append(c["close"])
        c = candles(secid, start, end, interval, info["engine"], info["market"], info["board"])
        if not c.empty:
            parts.append(c["close"])
        s = pd.concat(parts).sort_index() if parts else pd.Series(dtype=float)
    s = s[~s.index.duplicated(keep="last")].astype(float)
    s = s[s > 0]
    if adjust_splits and info["market"] != "index" and not s.empty:
        from core.analytics.metrics import adjust_splits as _adjust
        try:
            s = _adjust(s, splits(secid))
        except (ISSError, requests.RequestException):
            pass
    s.name = secid
    return s


# --------------------------------------------------------------------- страница бумаги
# Прежние основные режимы для подклейки дневных свечей страницы бумаги: как LEGACY_MAIN_BOARDS
# и TQTF (фонды до июня 2026 г.) — тот же набор, что LEGACY_MAIN_BOARDS в web/lib/symbol.js.
LEGACY_OHLC_BOARDS = LEGACY_MAIN_BOARDS | {"TQTF"}


def description(secid: str) -> dict:
    """ISS /securities/{secid}: {"description": {NAME (верхний регистр): value}, "boards": [dict строк boards]}.
    Тот же запрос, что resolve() (кэш общий). Нет режимов торгов — ISSError."""
    data = get_json(f"/securities/{secid}", ttl=TTL_REFERENCE)
    desc = block(data, "description")
    boards = block(data, "boards")
    if boards.empty:
        raise ISSError(f"Инструмент {secid} не найден на ISS")
    d = {}
    if not desc.empty and {"name", "value"} <= set(desc.columns):
        d = {str(k).upper(): v for k, v in zip(desc["name"], desc["value"]) if k}
    rows = boards.astype(object).where(boards.notna(), None).to_dict("records")
    return {"description": d, "boards": rows}


def issuer(secid: str) -> Optional[str]:
    """Эмитент: поле emitent_title точного совпадения secid в /securities?q= (как issuerOf в web/lib/symbol.js).
    Ошибка или нет записи — None."""
    try:
        data = get_json("/securities", {"q": secid, "limit": 100, "securities.columns": "secid,emitent_title"},
                        ttl=TTL_REFERENCE)
    except ISSError:
        return None
    df = block(data, "securities")
    if df.empty or "secid" not in df.columns or "emitent_title" not in df.columns:
        return None
    row = df[df["secid"] == secid]
    v = row["emitent_title"].iloc[0] if not row.empty else None
    return str(v) if v else None


def board_snapshot(secid: str, engine: str, market: str, board: str) -> dict:
    """Текущие данные режима: /engines/{e}/markets/{m}/boards/{b}/securities/{secid} (блоки securities,
    marketdata, marketdata_yields) -> {имя блока: dict первой строки | {}}."""
    data = get_json(f"/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}",
                    {"iss.only": "securities,marketdata,marketdata_yields"}, ttl=TTL_MARKET)
    out = {}
    for name in ("securities", "marketdata", "marketdata_yields"):
        df = block(data, name)
        out[name] = ({} if df.empty else
                     {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                      for k, v in df.iloc[0].to_dict().items()})
    return out


def index_candle_begin(secid: str) -> Optional[str]:
    """Начало дневных свечей индекса: candleborders (строка interval = 24, поле begin) -> 'YYYY-MM-DD';
    недоступно / нет строки — None (одна попытка, как indexCandleBegin в web/lib/symbol.js)."""
    try:
        data = get_json(f"/engines/stock/markets/index/securities/{secid}/candleborders", ttl=TTL_REFERENCE,
                        retries=1)
    except ISSError:
        return None
    df = block(data, "borders")
    if df.empty or "interval" not in df.columns or "begin" not in df.columns:
        return None
    row = df[pd.to_numeric(df["interval"], errors="coerce") == 24]
    if row.empty or not row["begin"].iloc[0]:
        return None
    return str(row["begin"].iloc[0])[:10]


def _iso(v) -> Optional[str]:
    s = str(v)[:10] if v else ""
    return s if len(s) == 10 and s[4] == "-" and not s.startswith("0000") else None


def daily_ohlc(secid: str, start: str = "1990-01-01", end: Optional[str] = None) -> pd.DataFrame:
    """Дневные OHLC основного режима (порт fetchOHLC из web/lib/symbol.js) -> DataFrame
    (индекс — даты, колонки open, high, low, close, volume).
    Акции, фонды, облигации, металлы: свечи interval=24 основного режима и прежних режимов того же рынка до
    history_from основного (LEGACY_OHLC_BOARDS — EQBR и др., TQTF у фондов — или та же board_group_id);
    при совпадении даты — основной режим. Индексы: дневные свечи рынка index с начала (candleborders),
    history (OPEN/HIGH/LOW/CLOSE) — только до него; candleborders недоступен — только history; volume — NaN.
    Свечи ISS уже скорректированы биржей на сплиты (web/CONTRACT.md) — не корректируются повторно.
    Строки без close > 0 отбрасываются, O/H/L ≤ 0 — NaN. attrs["split_adjusted"] = True."""
    info = description(secid)
    boards = info["boards"]
    prim = next((b for b in boards if str(b.get("is_primary")) in ("1", "1.0")), boards[0])
    engine, market = prim.get("engine"), prim.get("market")
    f = start or "1990-01-01"
    t = end or dt.date.today().isoformat()
    parts: list[pd.DataFrame] = []
    if market == "index":
        begin = index_candle_begin(secid)
        hist_till = t if begin is None else min(t, (pd.Timestamp(begin) - pd.Timedelta(days=1)).date().isoformat())
        if f <= hist_till:
            h = _paged(f"/history/engines/stock/markets/index/securities/{secid}",
                       {"from": f, "till": hist_till, "history.columns": "TRADEDATE,OPEN,HIGH,LOW,CLOSE"},
                       "history", ttl=_ttl_for(hist_till))
            if not h.empty:
                h = h.rename(columns=str.lower)
                h.index = pd.to_datetime(h["tradedate"]).dt.normalize()
                parts.append(h[["open", "high", "low", "close"]].assign(volume=np.nan))
        if begin is not None and max(begin, f) <= t:
            c = candles(secid, max(begin, f), t, "D", "stock", "index", None)
            if not c.empty:
                parts.append(c[["open", "high", "low", "close"]].assign(volume=np.nan))
    else:
        prim_from = _iso(prim.get("history_from"))
        segs = []
        for b in boards:
            if b is prim or b.get("engine") != engine or b.get("market") != market:
                continue
            hf, ht = _iso(b.get("history_from")), _iso(b.get("history_till"))
            if not hf or not prim_from or hf >= prim_from:
                continue
            same_group = b.get("board_group_id") is not None and b.get("board_group_id") == prim.get("board_group_id")
            if b.get("boardid") not in LEGACY_OHLC_BOARDS and not same_group:
                continue
            a, z = max(hf, f), min(t, ht or t, prim_from)
            if a <= z:
                segs.append((hf, b["boardid"], a, z))
        segs.sort()
        segs.append(("", prim["boardid"], max(prim_from, f) if prim_from else f, t))
        for _, board, a, z in segs:
            if a > z:
                continue
            c = candles(secid, a, z, "D", engine, market, board)
            if not c.empty:
                parts.append(c[["open", "high", "low", "close", "volume"]])
    cols = ["open", "high", "low", "close", "volume"]
    if not parts:
        df = pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="date"), dtype=float)
    else:
        df = pd.concat(parts).apply(pd.to_numeric, errors="coerce").astype(float)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[df["close"] > 0]
        for k in ("open", "high", "low"):
            df[k] = df[k].where(df[k] > 0)
        df.index = pd.DatetimeIndex(df.index, name="date")
        df = df[cols]
    df.attrs["split_adjusted"] = True
    return df


# --------------------------------------------------------------------- showcases
def _board_table(engine: str, market: str, board: str, sec_cols: list[str],
                 md_cols: list[str], extra_block: Optional[str] = None,
                 extra_cols: Optional[list[str]] = None) -> pd.DataFrame:
    data = get_json(f"/engines/{engine}/markets/{market}/boards/{board}/securities",
                    {"securities.columns": ",".join(sec_cols),
                     "marketdata.columns": ",".join(md_cols)}, ttl=TTL_MARKET)
    sec, md = block(data, "securities"), block(data, "marketdata")
    df = sec.merge(md, on="SECID", how="left", suffixes=("", "_md"))
    if extra_block:
        ex = block(data, extra_block)
        if not ex.empty:
            keep = ["SECID"] + [c for c in (extra_cols or []) if c in ex.columns]
            df = df.merge(ex[keep], on="SECID", how="left", suffixes=("", "_y"))
    return df


def stocks(board: str = "TQBR") -> pd.DataFrame:
    """Только акции и расписки (без паёв фондов)."""
    df = shares(board)
    return df[df["SECTYPE"].astype(str).isin(SHARE_TYPES)]


def shares(board: str = "TQBR") -> pd.DataFrame:
    df = _board_table("stock", "shares", board,
                      ["SECID", "SHORTNAME", "SECNAME", "ISIN", "LOTSIZE", "ISSUESIZE",
                       "PREVPRICE", "LISTLEVEL", "SECTYPE", "CURRENCYID"],
                      ["SECID", "LAST", "LASTTOPREVPRICE", "VALTODAY_RUR", "ISSUECAPITALIZATION",
                       "UPDATETIME"])
    df["PRICE"] = df["LAST"].fillna(df["PREVPRICE"])
    df["CAP"] = df["ISSUECAPITALIZATION"].fillna(df["PRICE"] * df["ISSUESIZE"])
    return df


# SECTYPE режима TQBR: 1 — обыкн. акции, 2 — привилег., D — депозитарные расписки;
# J — биржевые ПИФ, 9 / A / B — паи открытых / интервальных / закрытых ПИФ.
SHARE_TYPES = {"1", "2", "D"}
FUND_TYPES = {"J", "9", "A", "B"}
FUND_TYPE_LABELS = {"J": "БПИФ", "9": "ОПИФ", "A": "ИПИФ", "B": "ЗПИФ"}


def etfs() -> pd.DataFrame:
    """Паи фондов. С июня 2026 г. фонды торгуются в TQBR (ранее — TQTF), берём оба режима."""
    frames = []
    for board in ("TQBR", "TQTF"):
        try:
            df = shares(board)
        except ISSError:
            continue
        if board == "TQBR":
            df = df[df["SECTYPE"].astype(str).isin(FUND_TYPES)]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True).drop_duplicates("SECID") if frames else pd.DataFrame()
    if not df.empty:
        df["FUNDTYPE"] = df["SECTYPE"].astype(str).map(FUND_TYPE_LABELS).fillna("БПИФ")
    return df


def bonds(board: str = "TQOB") -> pd.DataFrame:
    """TQOB — ОФЗ, TQCB — корпоративные, TQIR — с плавающим купоном и т.п."""
    df = _board_table("stock", "bonds", board,
                      ["SECID", "SHORTNAME", "SECNAME", "ISIN", "FACEVALUE", "COUPONPERCENT",
                       "COUPONVALUE", "COUPONPERIOD", "NEXTCOUPON", "MATDATE", "OFFERDATE",
                       "ACCRUEDINT", "PREVPRICE", "LISTLEVEL", "FACEUNIT", "BONDTYPE"],
                      ["SECID", "LAST", "YIELD", "DURATION", "VALTODAY_RUR", "UPDATETIME"],
                      extra_block="marketdata_yields",
                      extra_cols=["EFFECTIVEYIELD", "DURATION", "ZSPREADBP", "GSPREADBP"])
    df["PRICE"] = df["LAST"].fillna(df["PREVPRICE"])
    dur_col = "DURATION" if "DURATION" in df else "DURATION_y"
    df["DURATION_Y"] = pd.to_numeric(df[dur_col], errors="coerce") / 365.25
    df["MATDATE"] = pd.to_datetime(df["MATDATE"], errors="coerce")
    df["YEARS_TO_MAT"] = (df["MATDATE"] - pd.Timestamp.today()).dt.days / 365.25
    return df


METALS = {"GLDRUB_TOM": "Золото (руб./г)", "SLVRUB_TOM": "Серебро (руб./г)",
          "PLDRUB_TOM": "Палладий (руб./г)", "PLTRUB_TOM": "Платина (руб./г)"}


def metals() -> pd.DataFrame:
    data = get_json("/engines/currency/markets/selt/boards/CETS/securities",
                    {"securities.columns": "SECID,SHORTNAME,PREVPRICE,LOTSIZE",
                     "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE,VALTODAY_RUR,UPDATETIME"},
                    ttl=TTL_MARKET)
    df = block(data, "securities").merge(block(data, "marketdata"), on="SECID", how="left")
    df = df[df["SECID"].isin(METALS)].copy()
    df["PRICE"] = df["LAST"].fillna(df["PREVPRICE"])
    df["NAME"] = df["SECID"].map(METALS)
    return df


def indices() -> pd.DataFrame:
    data = get_json("/engines/stock/markets/index/securities",
                    {"securities.columns": "SECID,SHORTNAME,NAME,CURRENCYID",
                     "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGEPRC,UPDATETIME"},
                    ttl=TTL_MARKET)
    return block(data, "securities").merge(block(data, "marketdata"), on="SECID", how="left")


def index_constituents(index_id: str = "IMOEX") -> pd.DataFrame:
    df = _paged(f"/statistics/engines/stock/markets/index/analytics/{index_id}",
                {"limit": 100}, "analytics", ttl=TTL_REFERENCE)
    if df.empty:
        return df
    last = df["tradedate"].max()
    return df[df["tradedate"] == last][["ticker", "shortnames", "weight"]]


# --------------------------------------------------------------------- cash flows
def dividends(secid: str) -> pd.DataFrame:
    """Дивиденды по данным ISS. В 2026 г. публичный метод перестал отдавать блок dividends —
    тогда берутся выплаты T-Invest API из файла сборщика public/data/dividends.json
    (без отменённых), а если там бумаги нет — локальный data/dividends.csv. Так же и при
    недоступности ISS
    (secid, registryclosedate, value)."""
    try:
        df = block(get_json(f"/securities/{secid}/dividends", ttl=TTL_REFERENCE), "dividends")
    except (ISSError, requests.RequestException):
        df = pd.DataFrame()              # ISS недоступен — локальные источники ниже
    if df.empty:
        from core.data import public_data
        df = public_data.dividends(secid)
    if df.empty:
        from core.config import DATA_DIR
        f = DATA_DIR / "dividends.csv"
        if f.exists():
            loc = pd.read_csv(f)
            df = loc[loc["secid"].str.upper() == secid.upper()].copy()
            df["currencyid"] = "RUB"
    if df.empty:
        return df
    df["registryclosedate"] = pd.to_datetime(df["registryclosedate"])
    return df.sort_values("registryclosedate")


def coupons(secid: str) -> pd.DataFrame:
    data = get_json(f"/statistics/engines/stock/markets/bonds/bondization/{secid}",
                    {"limit": 1000}, ttl=TTL_REFERENCE)
    df = block(data, "coupons")
    if not df.empty:
        df["coupondate"] = pd.to_datetime(df["coupondate"])
    return df


def total_return_series(secid: str, start: str = "2000-01-01", end: Optional[str] = None) -> pd.Series:
    """Акция/фонд с реинвестированием дивидендов (дивиденд в дату закрытия реестра)."""
    px = close_series(secid, start, end)
    if px.empty:
        return px
    dv = dividends(secid)
    if dv.empty:
        return px
    dv = dv[dv["currencyid"].isin(["RUB", "SUR"])] if "currencyid" in dv else dv
    per_day = dv.groupby("registryclosedate")["value"].sum()
    per_day.index = pd.to_datetime(per_day.index)
    # доход дня = (P_t + D_t) / P_{t-1}; дивиденд относим на ближайший торговый день ≥ даты
    d = pd.Series(0.0, index=px.index)
    for day, v in per_day.items():
        pos = px.index.searchsorted(day)
        if 0 < pos < len(px):
            d.iloc[pos] += v
    r = (px + d) / px.shift(1) - 1
    tr = (1 + r.fillna(0)).cumprod() * px.iloc[0]
    tr.name = f"{secid} TR"
    return tr


# --------------------------------------------------------------------- yield curve
def zcyc(date: Optional[str] = None) -> pd.DataFrame:
    """КБД Мосбиржи на дату: срок (лет) -> доходность (% годовых)."""
    params = {"date": date} if date else {}
    data = get_json("/engines/stock/zcyc", params,
                    ttl=None if date and pd.Timestamp(date) < pd.Timestamp.today() - pd.Timedelta(days=2)
                    else TTL_MARKET)
    yy = block(data, "yearyields")
    if yy.empty:
        return yy
    yy = yy[yy["tradedate"] == yy["tradedate"].max()] if "tradedate" in yy else yy
    return yy[["period", "value"]].astype(float).sort_values("period")


def kbd_rate(date: Optional[str] = None, years: float = 15.0) -> float:
    """Ставка КБД для срока `years` (доля). Пример: 15 лет на 11.01.2011 = 7,86 %."""
    c = zcyc(date)
    if c.empty:
        raise ISSError("КБД недоступна на эту дату")
    return float(np.interp(years, c["period"], c["value"]) / 100)
