"""
Данные и расчёты страницы бумаги для Streamlit (pages/2_Карточка_бумаги.py) — Python-аналог
web/lib/symbol.js + расчётов web/js/page-symbol.js (контракт — web/CONTRACT.md, «Этап 1»).

* load_symbol(secid, years) — загрузка: справочник, свечи, котировки (ISS через core/data/iss.py),
  дивиденды, фонды RusETFs, ночные рейтинги (файлы сборщика public/data/ через core/data/public_data.py).
  Каждый ключ грузится независимо: ошибка одного — значение None и текст в errors[ключ].
* report(data, bench, rf) — все расчёты блоков страницы; формулы — только из core/analytics/metrics.py.

Функции чистые (кэширование — в интерфейсе, st.cache_data), результаты picklable: dict / list / DataFrame /
Series / MetricsReport. Доли — в долях, кроме полей, названных в % (coupon_percent, комиссии фондов — как в
источнике). Даты в словарях — строки 'YYYY-MM-DD'. Модуль не импортирует Streamlit.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.analytics import metrics as m
from core.analytics.ranking import next_weekday
from core.data import iss, public_data

SYMBOL_CLASSES = {"share": "Акция", "fund": "Фонд", "bond": "Облигация", "index": "Индекс",
                  "metal": "Металл", "currency": "Валюта"}
# бенчмарк по умолчанию для класса (ключи каталога; как DEFAULT_BENCHMARKS в web/lib/symbol.js)
DEFAULT_BENCHMARKS = {"share": "MCFTR", "fund": "MCFTR", "bond_ofz": "RGBITR", "bond": "RUCBTRNS",
                      "index": "IMOEX", "metal": "GOLD_CBR"}
METAL_IDS = {"GLDRUB_TOM", "SLVRUB_TOM", "PLDRUB_TOM", "PLTRUB_TOM"}
RUB = {"RUB", "SUR", "RUR"}
DATA_KEYS = ("info", "ohlc", "snapshot", "dividends", "coupons", "fund", "rating")


# ------------------------------------------------------------------ помощники (как в symbol.js)
def _num(v) -> Optional[float]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _pos(v) -> Optional[float]:
    x = _num(v)
    return x if x is not None and x > 0 else None


def _first_num(*vs) -> Optional[float]:
    for v in vs:
        x = _num(v)
        if x is not None:
            return x
    return None


def _first_pos(*vs) -> Optional[float]:
    for v in vs:
        x = _pos(v)
        if x is not None:
            return x
    return None


def _iso(v) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, (pd.Timestamp, dt.date)):
        return str(pd.Timestamp(v).date())
    s = str(v)[:10]
    if len(s) != 10 or s.startswith("0000"):
        return None
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return None
    return s


def _currency(v) -> str:
    c = str(v).upper() if v else "RUB"
    return "RUB" if c in RUB else c


def _today() -> str:
    return dt.date.today().isoformat()


def _err(e: Exception) -> str:
    return str(e) or type(e).__name__


# ------------------------------------------------------------------ классификация
def classify(type_: Any = None, group: Any = None, secid: Any = None, market: Any = None,
             sectype: Any = None) -> Optional[str]:
    """Класс бумаги по полям ISS TYPE/GROUP, рынку режима и SECTYPE (как classify в web/lib/symbol.js):
    metal, index, bond, fund (*ppif*/*etf*, SECTYPE J/9/A/B), share (*share*/*depositary*, stock_dr,
    SECTYPE 1/2/D), currency; иначе None."""
    t, g = str(type_ or "").lower(), str(group or "").lower()
    st = "" if sectype is None else str(sectype).upper()
    if str(secid or "").upper() in METAL_IDS or "metal" in t or "metal" in g:
        return "metal"
    if "index" in t or "index" in g or market == "index":
        return "index"
    if "bond" in t or "bond" in g or market == "bonds":
        return "bond"
    if "ppif" in t or "etf" in t or "ppif" in g or "etf" in g or st in ("J", "9", "A", "B"):
        return "fund"
    if "share" in t or "depositary" in t or "share" in g or g == "stock_dr" or st in ("1", "2", "D"):
        return "share"
    if g.startswith("currency") or market == "selt":
        return "currency"
    return None


def default_benchmark(cls: Optional[str], secid: str = "", type_: Any = None) -> Optional[str]:
    """Бенчмарк по умолчанию: акции/фонды — MCFTR, ОФЗ — RGBITR, прочие облигации — RUCBTRNS,
    индексы — IMOEX (для самого IMOEX — MCFTR), металлы — GOLD_CBR, валюты и неизвестное — None."""
    if cls == "bond":
        ofz = "ofz" in str(type_ or "").lower() or (str(secid).startswith("SU") and str(secid)[2:3].isdigit())
        return DEFAULT_BENCHMARKS["bond_ofz"] if ofz else DEFAULT_BENCHMARKS["bond"]
    if cls == "index" and secid == "IMOEX":
        return "MCFTR"
    return DEFAULT_BENCHMARKS.get(cls) if cls else None


def _primary(boards: list[dict]) -> dict:
    return next((b for b in boards if str(b.get("is_primary")) in ("1", "1.0")), boards[0])


# ------------------------------------------------------------------ загрузка
def load_info(secid: str, raw: Optional[dict] = None) -> dict:
    """Шапка страницы (как fetchSecurityInfo): ISS /securities/{id} (description + boards) и эмитент.
    coupon_percent — % годовых (как в ISS), coupon_period — дней (COUPONPERIOD или round(364 / COUPONFREQUENCY)),
    face_value — в валюте номинала, first_trade_date — самая ранняя history_from режимов того же рынка."""
    sid = secid.strip().upper()
    raw = raw or iss.description(sid)          # raw — готовый ответ iss.description (без повторного запроса)
    d, boards = raw["description"], raw["boards"]
    prim = _primary(boards)
    cls = classify(d.get("TYPE"), d.get("GROUP"), sid, prim.get("market"))
    first = None
    for b in boards:
        if b.get("engine") != prim.get("engine") or b.get("market") != prim.get("market"):
            continue
        h = _iso(b.get("history_from"))
        if h and (first is None or h < first):
            first = h
    freq = _pos(d.get("COUPONFREQUENCY"))
    period = _pos(d.get("COUPONPERIOD"))
    if period is None and freq:
        period = float(round(364 / freq))
    list_level = _num(d.get("LISTLEVEL"))
    return {
        "secid": sid,
        "name": d.get("NAME") or d.get("SHORTNAME") or sid,
        "short_name": d.get("SHORTNAME") or sid,
        "isin": d.get("ISIN") or None,
        "cls": cls,
        "type_label": d.get("TYPENAME") or (SYMBOL_CLASSES.get(cls) if cls else None) or "Инструмент",
        "issuer": iss.issuer(sid),
        "list_level": int(list_level) if list_level is not None else None,
        "currency": _currency(d.get("FACEUNIT") or d.get("CURRENCYID")),
        "issue_date": _iso(d.get("ISSUEDATE")),
        "first_trade_date": first,
        "face_value": _pos(d.get("FACEVALUE")),
        "mat_date": _iso(d.get("MATDATE")),
        "coupon_percent": _num(d.get("COUPONPERCENT")),
        "coupon_period": int(period) if period is not None else None,
        "offer_date": (_iso(d.get("OFFERDATE")) or _iso(d.get("PUTOPTIONDATE")) or _iso(d.get("BUYBACKDATE"))
                       or _iso(d.get("CALLOPTIONDATE"))),
        "board": prim.get("boardid"),
        "engine": prim.get("engine"),
        "market": prim.get("market"),
        "benchmark": default_benchmark(cls, sid, d.get("TYPE")),
    }


def snapshot(secid: str, ohlc: Optional[pd.DataFrame] = None, boards: Optional[list] = None,
             fetch_candles: bool = True) -> dict:
    """Ключевые цифры (как fetchSnapshot): marketdata + securities (+ marketdata_yields) основного режима;
    high52/low52 — по high/low свечей за 365 дней до последней (нет high/low — по close). ohlc не передан —
    свечи за 380 дней (fetch_candles=False — без свечей, только marketdata). boards — готовые режимы
    из iss.description (без повторного запроса). change_pct — доля; облигации: цены — % номинала, bond.yield — доля, bond.duration — лет."""
    sid = secid.strip().upper()
    prim = _primary(boards or iss.description(sid)["boards"])
    engine, market, board = prim.get("engine"), prim.get("market"), prim.get("boardid")
    blocks = iss.board_snapshot(sid, engine, market, board)
    s, md, my = blocks.get("securities") or {}, blocks.get("marketdata") or {}, blocks.get("marketdata_yields") or {}
    if ohlc is None and fetch_candles:
        try:
            ohlc = iss.daily_ohlc(sid, (pd.Timestamp.today() - pd.Timedelta(days=380)).date().isoformat(),
                                  boards=boards)
        except Exception:  # noqa: BLE001 — без свечей: только marketdata
            ohlc = None
    o = ohlc if ohlc is not None and not ohlc.empty else None
    n_c = 0 if o is None else len(o)
    last_candle = float(o["close"].iloc[-1]) if n_c else None
    last_candle_date = str(o.index[-1].date()) if n_c else None
    date = _iso(md.get("TRADEDATE")) or _iso(md.get("SYSTIME")) or last_candle_date or _iso(s.get("PREVDATE"))
    last = _first_pos(md.get("LAST"), md.get("LCURRENTPRICE"), md.get("MARKETPRICE"), md.get("CURRENTVALUE"),
                      last_candle, s.get("PREVPRICE"))
    candle_prev = None
    if n_c:
        if last_candle_date == date:
            candle_prev = float(o["close"].iloc[-2]) if n_c > 1 else None
        else:
            candle_prev = last_candle
    cv, lc = _num(md.get("CURRENTVALUE")), _num(md.get("LASTCHANGE"))
    idx_prev = cv - lc if cv is not None and lc is not None else None
    prev_close = _first_pos(s.get("PREVPRICE"), idx_prev, candle_prev)
    change = last - prev_close if last is not None and prev_close is not None else None
    high52 = low52 = None
    if n_c:
        w = o[o.index >= o.index[-1] - pd.Timedelta(days=365)]
        hi = w["high"].where(np.isfinite(w["high"]), w["close"])
        lo = w["low"].where(np.isfinite(w["low"]), w["close"])
        high52, low52 = float(hi.max()), float(lo.min())
    cls = classify(secid=sid, market=market, sectype=s.get("SECTYPE"))
    issue_size = _pos(s.get("ISSUESIZE"))
    cap = _first_pos(md.get("ISSUECAPITALIZATION"), md.get("CAPITALIZATION") if market == "index" else None)
    if cap is None and cls == "share" and issue_size is not None and last is not None:
        cap = last * issue_size
    bond = None
    if market == "bonds":
        y = _first_num(md.get("YIELD"), my.get("EFFECTIVEYIELD"))
        dur = _first_pos(md.get("DURATION"), my.get("DURATION"))
        bond = {"yield": y / 100 if y is not None else None,
                "duration": dur / 365.25 if dur is not None else None,
                "accrued_int": _num(s.get("ACCRUEDINT")),
                "coupon_value": _num(s.get("COUPONVALUE")),
                "next_coupon": _iso(s.get("NEXTCOUPON"))}
    return {"date": date, "last": last, "prev_close": prev_close, "change": change,
            "change_pct": change / prev_close if change is not None and prev_close else None,
            "high52": high52, "low52": low52,
            "value_rub": _first_num(md.get("VALTODAY_RUR"), md.get("VALTODAY")),
            "market_cap": cap, "bond": bond}


def load_dividends(secid: str) -> list[dict]:
    """Дивиденды из public/data/dividends.json (T-Invest API), как loadDividends: ex_date — следующий рабочий
    день (пн–пт, без праздников) после last_buy_date, без неё — record_date (как ranking.ex_dividends);
    yield — доля (yield_value T-Invest — в %). Отменённые остаются с cancelled=True. Нет файла / бумаги — [].
    Сортировка по record_date."""
    data = public_data.load("dividends")
    rows = data.get(secid.strip().upper()) if isinstance(data, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        rec, value = _iso(r.get("record_date")), _num(r.get("value"))
        if not rec or value is None:
            continue
        lbd = _iso(r.get("last_buy_date"))
        y = _num(r.get("yield_value"))
        out.append({
            "record_date": rec,
            "ex_date": str(next_weekday(pd.Timestamp(lbd)).date()) if lbd else rec,
            "payment_date": _iso(r.get("payment_date")),
            "declared_date": _iso(r.get("declared_date")),
            "value": value,
            "currency": _currency(r.get("currency")),
            "yield": y / 100 if y is not None else None,
            "cancelled": bool(r.get("cancelled")),
        })
    return sorted(out, key=lambda x: x["record_date"])


def fund_info(secid: str) -> dict:
    """Фонд в справочнике RusETFs (public/data/rusetfs_funds.json) и его комиссия против рынка (как loadFundInfo):
    market — по торгуемым фондам (trade_status «Торгуется» или не указан) с commission_pct > 0 того же
    asset_class (нет записи фонда или класса — по всем торгуемым): min, p25, median, p75, max (линейная
    интерполяция, np.percentile), n, class_median (= median), market_median — медиана всех торгуемых.
    Комиссии — в % годовых, как commission_pct. Нет файла — {info: None, market: {… None, n: 0}}."""
    empty = {"min": None, "p25": None, "median": None, "p75": None, "max": None, "n": 0,
             "class_median": None, "market_median": None}
    data = public_data.load("rusetfs_funds")
    rows = [r for r in data if isinstance(r, dict) and r.get("ticker")] if isinstance(data, list) else []
    if not rows:
        return {"info": None, "market": empty}
    sid = secid.strip().upper()
    info = next((dict(r) for r in rows if str(r["ticker"]).upper() == sid), None)
    traded = [r for r in rows if (r.get("trade_status") is None or r.get("trade_status") == "Торгуется")
              and (_num(r.get("commission_pct")) or 0) > 0]
    same = [r for r in traded if r.get("asset_class") == info["asset_class"]] if info and info.get("asset_class") \
        else traded
    fees = np.array([float(r["commission_pct"]) for r in same])
    allf = np.array([float(r["commission_pct"]) for r in traded])
    market_median = float(np.percentile(allf, 50)) if len(allf) else None
    if not len(fees):
        return {"info": info, "market": {**empty, "market_median": market_median}}
    med = float(np.percentile(fees, 50))
    return {"info": info, "market": {
        "min": float(fees.min()), "p25": float(np.percentile(fees, 25)), "median": med,
        "p75": float(np.percentile(fees, 75)), "max": float(fees.max()), "n": int(len(fees)),
        "class_median": med, "market_median": market_median}}


def rating(secid: str) -> Optional[dict]:
    """Запись items[secid] из symbol_stats.json (копия) + n_class (classes[класс].n), updated, total_return
    (верхние поля файла). Бумаги нет в рейтинге — None; нет файла — ValueError (текст — в errors)."""
    stats = public_data.symbol_stats()
    if stats is None:
        raise ValueError("Рейтинг появится после ночного пересчёта (public/data/symbol_stats.json).")
    it = stats["items"].get(secid.strip().upper())
    if not isinstance(it, dict):
        return None
    cls_info = (stats.get("classes") or {}).get(it.get("class")) or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in it.items()}
    out.update({"n_class": cls_info.get("n"), "updated": stats.get("updated"),
                "total_return": stats.get("total_return")})
    return out


def load_symbol(secid: str, years: Optional[int] = None) -> dict:
    """Все данные страницы бумаги: {secid, info, ohlc, snapshot, dividends, coupons, fund, rating, errors}.
    years — глубина свечей в календарных годах от сегодня (None — вся история с 1990 г.).
    Ключи грузятся независимо: ошибка — значение None и текст в errors[ключ]. coupons — только для облигаций
    (иначе None), fund — для фондов и бумаг неизвестного класса, найденных в RusETFs (иначе None).
    Справочник ISS (/securities/{id}) запрашивается один раз; его сбой (ISS недоступен, бумаги нет) — info,
    ohlc и snapshot = None с той же причиной, другие запросы ISS не делаются (файлы public/data — читаются).
    snapshot не перезапрашивает свечи, если ohlc не загрузился."""
    sid = str(secid or "").strip().upper()
    out: dict = {"secid": sid, **{k: None for k in DATA_KEYS}, "errors": {}}
    start = "1990-01-01" if years is None else \
        (pd.Timestamp.today().normalize() - pd.DateOffset(years=int(years))).date().isoformat()

    def run(key, fn):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001 — ключи независимы
            out[key] = None
            out["errors"][key] = _err(e)

    # справочник ISS — один запрос; его режимы торгов передаются свечам и котировкам
    try:
        raw = iss.description(sid)
    except Exception as e:  # noqa: BLE001 — ISS недоступен или бумаги нет: остальные запросы ISS не делаются
        raw = None
        for key in ("info", "ohlc", "snapshot"):
            out["errors"][key] = _err(e)
    if raw is not None:
        run("info", lambda: load_info(sid, raw))

        def _ohlc():
            o = iss.daily_ohlc(sid, start, boards=raw["boards"])
            if o.empty:
                raise ValueError(f"ISS не вернул истории торгов {sid}.")
            return o
        run("ohlc", _ohlc)
        run("snapshot", lambda: snapshot(sid, out["ohlc"], boards=raw["boards"], fetch_candles=False))
    run("dividends", lambda: load_dividends(sid))
    cls = (out["info"] or {}).get("cls")
    if cls == "bond":
        def _coupons():
            c = iss.coupons(sid)
            return c if not c.empty else None
        run("coupons", _coupons)

    def _fund():
        fi = fund_info(sid)
        return fi if cls == "fund" or (cls is None and fi["info"] is not None) else None
    if cls in ("fund", None):
        run("fund", _fund)
    run("rating", lambda: rating(sid))
    return out


def load_benchmark(key: str, start: Optional[str] = None) -> pd.Series:
    """Ряд бенчмарка для страницы бумаги — только живые данные, без демо-подмены universe.load_series:
    специальные ключи каталога (GOLD_CBR, RUONIA — ЦБ; CORP_CHAIN — склейка) — universe._load_live;
    прочие (индексы и бумаги ISS) — дневные цены закрытия iss.daily_ohlc(key, start)["close"].
    start — 'YYYY-MM-DD' (None — вся история). Сбой или пустой ряд — исключение (интерфейс показывает
    причину и строит страницу без бенчмарка)."""
    from core.data import universe
    k = str(key or "").strip().upper()
    if not k:
        raise ValueError("не задан бенчмарк")
    f = start or "1990-01-01"
    spec = universe.CATALOG.get(k)
    if spec is not None and spec.source in ("cbr", "chain"):
        s = universe._load_live(k, f, None, False)
    else:
        s = iss.daily_ohlc(k, f)["close"]
    s = s.dropna().astype(float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s > 0]
    if s.empty:
        raise ValueError(f"нет данных бенчмарка {k}")
    s.name = k
    return s


# бенчмарк отклоняется, если он отстал от бумаги больше чем на столько дней или реже дневного
BENCH_MAX_LAG_DAYS = 10
BENCH_MAX_STEP_DAYS = 5


def check_benchmark(bench: Optional[pd.Series], last_date: pd.Timestamp) -> Optional[str]:
    """Причина отклонить бенчмарк (текст) или None, если годится: пустой ряд; последняя дата раньше последней
    даты бумаги больше чем на BENCH_MAX_LAG_DAYS календарных дней; медианный шаг дат > BENCH_MAX_STEP_DAYS
    (реже дневного — например, месячный демо-ряд)."""
    if bench is None:
        return None
    b = pd.Series(bench).dropna()
    name = bench.name or "бенчмарк"
    if len(b) < 2:
        return f"Бенчмарк {name}: недостаточно данных."
    idx = pd.DatetimeIndex(b.index).sort_values()
    lag = (pd.Timestamp(last_date) - idx[-1]).days
    if lag > BENCH_MAX_LAG_DAYS:
        return (f"Бенчмарк {name}: данные заканчиваются {idx[-1].date():%d.%m.%Y}, на {lag} дн. раньше бумаги — "
                "сравнение не показано.")
    step = float(pd.Series(idx).diff().dt.days.median())
    if step > BENCH_MAX_STEP_DAYS:
        return f"Бенчмарк {name}: ряд реже дневного (медианный шаг {step:.0f} дн.) — сравнение не показано."
    return None


# ------------------------------------------------------------------ расчёты
def dividend_stats(divs: Optional[list], close: Optional[pd.Series], as_of: Optional[str] = None) -> dict:
    """Статистика дивидендов (как dividendStats в web/lib/symbol.js) без отменённых и не в рублях,
    на последнюю дату close (нет цен — сегодня; as_of — явная дата):
    ttm_value — сумма с ex_date за 365 дн. (ex_date > asOf − 365, ≤ asOf); ttm_yield = ttm_value / последняя цена
    (доля; нет цены — None); by_year — [{year, value}] сумм по годам ex_date; by_month — число выплат по месяцам
    ex_date (янв … дек); growth_streak — число подряд идущих лет, считая назад от последнего полного года, где
    сумма больше суммы предыдущего года; год первой выплаты ростом не считается (год без выплат внутри
    истории — сумма 0); payouts_per_year — среднее число выплат за последние 3 полных года (не раньше первой
    выплаты); upcoming — выплаты с ex_date позже даты расчёта."""
    px = close.dropna().astype(float).sort_index() if close is not None else pd.Series(dtype=float)
    if as_of is None:
        as_of = str(px.index[-1].date()) if len(px) else _today()
    pos_px = px[px > 0]
    last_px = float(pos_px.iloc[-1]) if len(pos_px) else None

    def ex(d):
        return d.get("ex_date") or d.get("exDate")
    valid = [d for d in (divs or []) if isinstance(d, dict) and not d.get("cancelled") and _iso(ex(d))
             and _num(d.get("value")) is not None and _currency(d.get("currency")) == "RUB"]
    past = [d for d in valid if _iso(ex(d)) <= as_of]
    upcoming = [dict(d) for d in valid if _iso(ex(d)) > as_of]
    ttm_from = str((pd.Timestamp(as_of) - pd.Timedelta(days=365)).date())
    ttm_value = float(sum(_num(d["value"]) for d in past if _iso(ex(d)) > ttm_from))
    totals: dict[int, float] = {}
    counts: dict[int, int] = {}
    by_month = [0] * 12
    for d in past:
        e = _iso(ex(d))
        y = int(e[:4])
        totals[y] = totals.get(y, 0.0) + _num(d["value"])
        counts[y] = counts.get(y, 0) + 1
        by_month[int(e[5:7]) - 1] += 1
    years = sorted(totals)
    last_full = int(as_of[:4]) - 1
    streak = 0
    if years:
        y = last_full
        while y > years[0] and totals.get(y, 0.0) > totals.get(y - 1, 0.0):
            streak += 1
            y -= 1
    payouts = None
    if years:
        span = [y for y in (last_full - 2, last_full - 1, last_full) if y >= years[0]]
        if span:
            payouts = sum(counts.get(y, 0) for y in span) / len(span)
    return {"as_of": as_of, "ttm_value": ttm_value,
            "ttm_yield": ttm_value / last_px if last_px else None,
            "by_year": [{"year": y, "value": totals[y]} for y in years],
            "by_month": by_month, "growth_streak": streak, "payouts_per_year": payouts,
            "upcoming": upcoming}


def align_benchmark(bench: Optional[pd.Series], dates: pd.DatetimeIndex) -> Optional[pd.Series]:
    """Бенчмарк на даты бумаги: значение на дату — последнее известное значение бенчмарка ≤ этой даты
    (as-of; дни без торгов бенчмарка — предыдущее значение), только начиная с первой даты бенчмарка и до
    последней даты бумаги. Меньше 2 точек — None."""
    if bench is None or not len(dates):
        return None
    b = pd.Series(bench).dropna().astype(float).sort_index()
    b = b[~b.index.duplicated(keep="last")]
    b = b[b > 0]
    if b.empty:
        return None
    d = dates[dates >= b.index[0]]
    out = b.reindex(b.index.union(d)).ffill().reindex(d).dropna()
    out.name = bench.name
    return out if len(out) >= 2 else None


def report(data: dict, bench: Optional[pd.Series], rf: m.RateLike) -> dict:
    """Все расчёты блоков страницы бумаги (как web/js/page-symbol.js), формулы — core/analytics/metrics.py.

    data  — результат load_symbol; bench — ряд цен бенчмарка (DatetimeIndex) или None (бумага сама бенчмарк /
            не загрузился); выравнивается по датам бумаги (align_benchmark); rf — годовая ставка, доля.
    Ключи: series — ряд для доходностей (для акций с рублёвыми неотменёнными дивидендами — полная доходность,
    metrics.total_return_series, иначе цена закрытия), total_return — флаг, close — цена закрытия,
    benchmark — выровненный бенчмарк | None; periods {security, benchmark} (period_returns); momentum (по close);
    monthly (monthly_grid по series); metrics {security, benchmark} — MetricsReport (compute_all, freq='D';
    бенчмарка — compute_all(bench, bench)); drawdowns {top (5), current, avg, ulcer, max, series,
    benchmark_series}; volatility {rolling, rolling_benchmark (21 день, ×√252), from, to, close_to_close,
    parkinson, garman_klass, rogers_satchell, yang_zhang — по OHLC за последний год (period_start '1Y')};
    dividend_stats (только акции, иначе None); errors — {блок: текст}. Нет блока — None.
    metrics_common {security, benchmark} — compute_all (freq='D') по общему периоду: только даты, где есть и бумага,
    и бенчмарк (пересечение, без протягивания), common_from / common_to — его границы; нет бенчмарка — None.
    Бенчмарк отклоняется (check_benchmark: отстал > 10 дн. от бумаги или реже дневного) — считается
    отсутствующим, причина — errors["benchmark"]."""
    out: dict = {k: None for k in ("series", "close", "benchmark", "periods", "momentum", "monthly", "metrics",
                                   "drawdowns", "volatility", "dividend_stats", "metrics_common",
                                   "common_from", "common_to")}
    out["total_return"] = False
    out["errors"] = {}
    ohlc = data.get("ohlc")
    cls = (data.get("info") or {}).get("cls")
    divs = data.get("dividends") or []
    if ohlc is None or ohlc.empty:
        out["errors"]["series"] = (data.get("errors") or {}).get("ohlc") or "нет истории торгов"
        return out
    close = ohlc["close"].dropna().astype(float)
    close = close[close > 0]
    close.name = data.get("secid")
    series = close
    if cls == "share":
        rub = [d for d in divs if not d.get("cancelled") and _currency(d.get("currency")) == "RUB"]
        if rub:
            series = m.total_return_series(close, rub)
            out["total_return"] = True
    out["series"], out["close"] = series, close
    why = check_benchmark(bench, series.index[-1])
    if why:
        out["errors"]["benchmark"] = why
        bench = None
    b = align_benchmark(bench, series.index)
    out["benchmark"] = b

    def run(key, fn):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001 — блоки независимы
            out[key] = None
            out["errors"][key] = _err(e)

    run("periods", lambda: {"security": m.period_returns(series),
                            "benchmark": m.period_returns(b) if b is not None else None})
    run("momentum", lambda: m.momentum(close))
    run("monthly", lambda: m.monthly_grid(series))

    def _metrics():
        if len(series) < 3:
            raise ValueError("слишком короткая история торгов")
        sec = m.compute_all(series, b, rf, freq="D")
        bm = m.compute_all(b, b, rf, freq="D") if b is not None and len(b) >= 3 else None
        return {"security": sec, "benchmark": bm}
    run("metrics", _metrics)

    def _common():
        braw = pd.Series(bench).dropna().astype(float)
        braw = braw[~braw.index.duplicated(keep="last")].sort_index()
        common = series.index.intersection(braw.index)
        if len(common) < 3:
            raise ValueError("у бумаги и бенчмарка меньше трёх общих дат")
        sc, bc = series.loc[common], braw.loc[common]
        out["common_from"], out["common_to"] = str(common[0].date()), str(common[-1].date())
        return {"security": m.compute_all(sc, bc, rf, freq="D"), "benchmark": m.compute_all(bc, bc, rf, freq="D")}
    if bench is not None:
        run("metrics_common", _common)
    run("drawdowns", lambda: {
        "top": m.top_drawdowns(series, 5), "current": m.current_drawdown(series),
        "avg": m.avg_drawdown(series), "ulcer": m.ulcer_index(series), "max": m.max_drawdown(series),
        "series": m.drawdown_series(series),
        "benchmark_series": m.drawdown_series(b) if b is not None else None})

    def _vol():
        frm = m.period_start(ohlc.index, "1Y") or str(ohlc.index[0].date())
        y1 = ohlc[ohlc.index >= pd.Timestamp(frm)]
        c1 = y1["close"].dropna()
        c1 = c1[c1 > 0]
        r1 = m.to_returns(c1)
        return {"rolling": m.rolling_volatility(series, 21, 252),
                "rolling_benchmark": m.rolling_volatility(b, 21, 252) if b is not None else None,
                "from": str(y1.index[0].date()), "to": str(y1.index[-1].date()),
                "close_to_close": m.volatility(r1, 252) if len(r1) >= 2 else float("nan"),
                "parkinson": m.parkinson(y1, 252), "garman_klass": m.garman_klass(y1, 252),
                "rogers_satchell": m.rogers_satchell(y1, 252), "yang_zhang": m.yang_zhang(y1, 252)}
    run("volatility", _vol)
    if cls == "share":
        run("dividend_stats", lambda: dividend_stats(divs, close))
    return out
