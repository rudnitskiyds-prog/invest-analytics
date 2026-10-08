"""
Сборщик данных для статического сайта.

Раз в день (GitHub Actions / Yandex Cloud Function / cron) забирает ряды, которые
браузер пользователя не может получить сам (у cbr.ru нет CORS), и сохраняет их
в public/data/*.json. Сайт читает эти файлы как обычные статические ресурсы.

    python -m scripts.collect_data            # все источники
    python -m scripts.collect_data --only cbr_gold ruonia

Формат каждого файла:
    {"id": ..., "title": ..., "unit": ..., "source": ..., "updated": ISO-время,
     "first": "YYYY-MM-DD", "last": "YYYY-MM-DD", "data": [["YYYY-MM-DD", value], ...]}

Источники T-Invest API (kind = "tinvest") требуют токена в переменной окружения
TINVEST_TOKEN (только чтение). Их файлы — {"updated", "source": "T-Invest API", "data": ...}:
    tinvest_shares.json  — акции TQBR: тикер, ISIN, сектор, лот, uid, asset_uid, флаги;
    tinvest_etfs.json    — фонды TQBR/TQTF: тип активов, fixed_commission (% год.), …;
    dividends.json       — {"TICKER": [выплаты по возрастанию record_date]}, 2008-01-01 … сегодня+365 дн.;
    fundamentals.json    — {"TICKER": {pe, pb, …}} (соответствие полям proto — FUNDAMENTAL_FIELDS).
Без токена эти источники получают статус {"ok": false, "skipped": true} и сбоем не считаются.

Источник RusETFs (kind = "rusetfs", токен не нужен) — JSON-API скринера rusetfs.com:
    rusetfs_funds.json   — {"updated", "source": "RusETFs (rusetfs.com)", "source_url",
                            "data": [записи core.data.rusetfs.normalize: ticker, УК, комиссия
                            commission_pct (% год.), СЧА aum_rub (руб.), класс активов, …]}.
Источник symbol_stats (kind = "iss", default = False — только явно: --only symbol_stats) — ночной
расчёт рейтингов по месячным свечам ISS (core/analytics/ranking.py):
    symbol_stats.json    — {"updated", "source": "ISS MOEX (расчёт ИнвестАналитики)", "rf", "window",
                            "classes": {"share": {"n"}, "fund": {"n"}}, "items": {"SBER": {"class",
                            sharpe, sortino, omega, calmar, martin, cagr, volatility, max_drawdown, months,
                            "rank": {…}, "score"}}}; котировки в файл не пишутся.
Порядок сбора: ряды ЦБ -> RusETFs -> T-Invest -> ISS (рейтинги).
    python -m scripts.collect_data --skip symbol_stats   # исключить источник

Если источник не ответил, прежний файл остаётся нетронутым, а в status.json
записывается ошибка — сайт продолжает работать на последних успешных данных.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

from core.config import ROOT, RUSETFS_URL
from core.data import cbr, rusetfs, tinvest

OUT = ROOT / "public" / "data"
START = "2008-01-01"


def _rows(s: pd.Series, digits: int) -> list:
    s = s.dropna().sort_index()
    return [[d.strftime("%Y-%m-%d"), round(float(v), digits)] for d, v in s.items()]


SOURCES: dict[str, dict] = {
    "cbr_gold": {
        "title": "Золото, учётная цена Банка России",
        "unit": "руб./г",
        "source": "https://www.cbr.ru/scripts/xml_metall.asp",
        "load": lambda: cbr.gold_price(START),
        "digits": 2,
        "min_rows": 3000,
    },
    "ruonia_rate": {
        "title": "Ставка RUONIA",
        "unit": "% годовых",
        "source": "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx (RuoniaXML)",
        "load": lambda: cbr.ruonia_rates(START),
        "digits": 4,
        "min_rows": 3000,
    },
    "ruonia_index": {
        "title": "Индекс денежного рынка (накопленная RUONIA, база 100)",
        "unit": "пункты",
        "source": "расчёт по RUONIA: I_t = I_{t-1} × (1 + r_{t-1} × дней / 365)",
        "load": lambda: cbr.ruonia_index(START),
        "digits": 5,
        "min_rows": 3000,
    },
    "key_rate": {
        "title": "Ключевая ставка Банка России",
        "unit": "доля годовых",
        "source": "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx (KeyRateXML)",
        "load": lambda: cbr.key_rate("2013-09-13"),
        "digits": 4,
        "min_rows": 2000,
    },
}


# ------------------------------------------------------------------ T-Invest API
TINVEST_SOURCE = "T-Invest API"
MAX_FAIL_SHARE = 0.20          # доля сбоев по тикерам / пачкам, после которой источник = сбой

# короткое имя в fundamentals.json -> поле GetAssetFundamentalsResponse.StatisticResponse
# (instruments.proto). Значения как есть (0 -> null по документации API), без пересчёта.
FUNDAMENTAL_FIELDS = {
    "pe": "pe_ratio_ttm", "ps": "price_to_sales_ttm", "pb": "price_to_book_ttm",
    "p_fcf": "price_to_free_cash_flow_ttm", "ev_ebitda": "ev_to_ebitda_mrq", "ev_sales": "ev_to_sales",
    "nd_ebitda": "net_debt_to_ebitda", "debt_equity": "total_debt_to_equity_mrq",
    "roe": "roe", "roa": "roa", "roic": "roic", "net_margin": "net_margin_mrq",
    "market_cap": "market_capitalization", "free_float": "free_float",
    "shares_outstanding": "shares_outstanding", "revenue_ttm": "revenue_ttm",
    "ebitda_ttm": "ebitda_ttm", "net_income_ttm": "net_income_ttm", "eps_ttm": "eps_ttm",
    "fcf_ttm": "free_cash_flow_ttm", "dividend_yield_ttm": "dividend_yield_daily_ttm",
    "dividend_rate_ttm": "dividend_rate_ttm", "payout_ratio": "dividend_payout_ratio_fy",
    "five_years_avg_dividend_yield": "five_years_average_dividend_yield", "beta": "beta",
    "high_52w": "high_price_last_52_weeks", "low_52w": "low_price_last_52_weeks",
    "currency": "currency", "fiscal_period_end": "fiscal_period_end_date",
}


TINVEST_BUDGET_MIN = 12.0      # общий бюджет времени на все источники T-Invest, мин
MAX_CONSECUTIVE_FAILS = 10     # подряд сбоев сервиса (5xx/429/сеть) — источник прерывается
DIVIDEND_RETRIES = 2           # повторов на один GetDividends (вызовов сотни)


class Skipped(Exception):
    """Источник пропущен (нет токена) — не сбой."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _write(name: str, payload: dict, indent: int | None = None) -> None:
    """Атомарная запись: при сбое посреди записи прежний файл не портится, .tmp удаляется."""
    tmp = OUT / f".{name}.json.tmp"
    text = (json.dumps(payload, ensure_ascii=False, indent=indent) if indent is not None
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(OUT / f"{name}.json")
    except BaseException:
        tmp.unlink(missing_ok=True)      # не оставлять мусор: workflow делает git add public/data
        raise


def _previous(name: str) -> dict:
    """Прежний файл источника (или {}), для сравнения data и подстановки пропусков."""
    try:
        prev = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8"))
        return prev if isinstance(prev, dict) else {}
    except (OSError, ValueError):
        return {}


def _client(ctx: dict) -> "tinvest.TInvestClient":
    if "client" not in ctx:
        if not os.environ.get("TINVEST_TOKEN", "").strip():
            raise Skipped("нет токена")
        c = tinvest.TInvestClient()
        # бюджет времени — по часам клиента (в тестах они виртуальные)
        c.deadline = c._clock() + ctx.get("budget_min", TINVEST_BUDGET_MIN) * 60
        ctx["client"] = c
    return ctx["client"]


def _shares(ctx: dict) -> list[dict]:
    """Акции TQBR: из текущего прогона (источник tinvest_shares) или свежим запросом.
    Если Shares упал или список не прошёл проверку здравости, зависимые источники
    (dividends, fundamentals) тоже получают сбой без повторного запроса Shares;
    их прежние файлы остаются."""
    if "shares_error" in ctx:
        raise RuntimeError(f"список акций не прошёл проверку ({ctx['shares_error']})")
    if "shares" not in ctx:
        client = _client(ctx)
        try:
            rows = tinvest.shares(client)
        except Exception as e:  # noqa: BLE001
            ctx["shares_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            raise
        need = SOURCES.get("tinvest_shares", {}).get("min_rows", 150)
        if len(rows) < need:
            ctx["shares_error"] = f"акций {len(rows)} < {need}"
            raise ValueError(f"слишком мало акций: {len(rows)} < {need} — ответ похож на ошибку")
        ctx["shares"] = rows
    return ctx["shares"]


def _check(what: str, n: int, need: int) -> None:
    if n < need:
        raise ValueError(f"слишком мало {what}: {n} < {need} — ответ похож на ошибку")


def _tolerant(items: list, fn: Callable, label: Callable, what: str) -> tuple[list, list]:
    """fn(item) по каждому элементу; ошибка элемента — лог и пропуск. Источник — сбой, если:
    доля сбоев > MAX_FAIL_SHARE; MAX_CONSECUTIVE_FAILS сбоев сервиса подряд (5xx/429/сеть —
    TInvestError.transient; 4xx по конкретной бумаге в серию не входят); исчерпан бюджет времени.
    Возвращает ([(item, результат)], [item со сбоем])."""
    res, failed, streak = [], [], 0
    for it in items:
        try:
            res.append((it, fn(it)))
            streak = 0
        except Exception as e:  # noqa: BLE001
            if getattr(e, "budget", False):
                raise RuntimeError(f"исчерпан бюджет времени T-Invest ({what}: обработано "
                                   f"{len(res) + len(failed)} из {len(items)})") from None
            failed.append(it)
            streak = streak + 1 if getattr(e, "transient", True) else 0
            if len(failed) <= 20:        # не засоряем лог Actions при массовом сбое
                print(f"  пропуск {what} {label(it)}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            if streak >= MAX_CONSECUTIVE_FAILS:
                raise RuntimeError(f"{streak} сбоев сервиса подряд ({what}) — источник прерван; "
                                   f"последний: {type(e).__name__}: {str(e)[:200]}") from None
    if len(failed) > 20:
        print(f"  … всего пропусков {what}: {len(failed)}", file=sys.stderr)
    if items and len(failed) / len(items) > MAX_FAIL_SHARE:
        raise RuntimeError(f"сбоев {len(failed)} из {len(items)} ({what}) — больше {MAX_FAIL_SHARE:.0%}")
    return res, failed


def tinvest_shares(ctx: dict, spec: dict) -> tuple:
    rows = sorted(_shares(ctx), key=lambda r: r["ticker"] or "")
    _check("акций", len(rows), spec["min_rows"])          # порог _shares задан SOURCES
    return rows, len(rows)


def tinvest_etfs(ctx: dict, spec: dict) -> tuple:
    rows = sorted(tinvest.etfs(_client(ctx)), key=lambda r: r["ticker"] or "")
    _check("фондов", len(rows), spec["min_rows"])
    return rows, len(rows)


# поля выплаты, которые не сохраняем: close_price — котировка Мосбиржи (в git их не храним)
DIVIDEND_DROP = ("close_price",)


def tinvest_dividends(ctx: dict, spec: dict) -> tuple:
    """Выплаты по всем акциям из tinvest_shares. Для тикеров со сбоем запроса подставляются
    записи из прежнего dividends.json; в статус — число и список (до 20) таких тикеров."""
    client = _client(ctx)
    till = (dt.date.today() + dt.timedelta(days=365)).isoformat()
    items = [r for r in _shares(ctx) if r.get("uid") and r.get("ticker")]
    got, failed = _tolerant(
        items, lambda r: tinvest.dividends(client, r["uid"], START, till, retries=DIVIDEND_RETRIES),
        lambda r: r["ticker"], "дивидендов")
    data: dict[str, list] = {}
    for share, rows in got:
        if rows:
            data.setdefault(share["ticker"], []).extend(
                {k: v for k, v in r.items() if k not in DIVIDEND_DROP} for r in rows)
    prev = _previous("dividends").get("data")
    prev = prev if isinstance(prev, dict) else {}
    missing = sorted({r["ticker"] for r in failed})
    reused = 0
    for t in missing:
        if isinstance(prev.get(t), list) and prev[t] and t not in data:
            data[t] = [{k: v for k, v in r.items() if k not in DIVIDEND_DROP}
                       for r in prev[t] if isinstance(r, dict)]
            reused += 1
    data = {t: tinvest.dedup_dividends(rows) for t, rows in sorted(data.items())}
    _check("тикеров с дивидендами", len(data), spec["min_rows"])
    extra = {"missing": len(missing), "missing_tickers": missing[:20], "reused_previous": reused} \
        if missing else {}
    return data, len(data), extra


def tinvest_fundamentals(ctx: dict, spec: dict) -> tuple:
    client = _client(ctx)
    by_asset: dict[str, list[str]] = {}
    for r in _shares(ctx):
        if r.get("asset_uid") and r.get("ticker"):
            by_asset.setdefault(r["asset_uid"], []).append(r["ticker"])
    uids = list(by_asset)
    n = tinvest.FUNDAMENTALS_BATCH
    batches = [uids[i:i + n] for i in range(0, len(uids), n)]
    got, _ = _tolerant(batches, lambda b: tinvest.fundamentals(client, b),
                       lambda b: f"пачка из {len(b)}", "фундаментальных показателей")
    data: dict[str, dict] = {}
    for _, recs in got:
        for uid, rec in recs.items():
            short = {k: rec.get(src) for k, src in FUNDAMENTAL_FIELDS.items()}
            if not any(v is not None for k, v in short.items() if k != "currency"):
                continue                     # пустая запись — данных по активу нет
            for ticker in by_asset.get(uid, []):
                data[ticker] = short
    _check("фундаментальных записей", len(data), spec["min_rows"])
    return dict(sorted(data.items())), len(data)


def collect_tinvest(name: str, spec: dict, ctx: dict) -> dict:
    left = ctx["client"]._left() if "client" in ctx else None
    if left is not None and left <= 0:
        raise RuntimeError("исчерпан бюджет времени T-Invest — источник не запускался")
    data, n, *extra = spec["collect"](ctx, spec)
    prev = _previous(name)
    # updated меняется только при изменении данных: не перекоммичивать большие файлы ежедневно
    updated = prev.get("updated") if prev.get("data") == data and prev.get("updated") else _now()
    _write(name, {"updated": updated, "source": TINVEST_SOURCE, "data": data})
    return {"ok": True, "rows": n, "last": dt.date.today().isoformat(), **(extra[0] if extra else {})}


# порядок важен: акции -> фонды -> дивиденды -> фундаментальные показатели
# (RusETFs добавляется в SOURCES ниже, но main() всё равно собирает его до T-Invest)
SOURCES.update({
    "tinvest_shares": {"kind": "tinvest", "title": "Акции TQBR (справочник T-Invest)",
                       "collect": tinvest_shares, "min_rows": 150},
    "tinvest_etfs": {"kind": "tinvest", "title": "Фонды TQBR/TQTF (справочник T-Invest)",
                     "collect": tinvest_etfs, "min_rows": 50},
    "dividends": {"kind": "tinvest", "title": "Дивиденды (T-Invest GetDividends)",
                  "collect": tinvest_dividends, "min_rows": 50},
    "fundamentals": {"kind": "tinvest", "title": "Фундаментальные показатели (T-Invest)",
                     "collect": tinvest_fundamentals, "min_rows": 100},
})


# ------------------------------------------------------------------ RusETFs
RUSETFS_SOURCE = "RusETFs (rusetfs.com)"
RUSETFS_MIN_COMMISSION_SHARE = 0.80   # доля фондов с комиссией, ниже — ответ похож на ошибку


def _rusetfs_session():
    """Сессия HTTP для RusETFs (в тестах подменяется фейковой)."""
    return requests.Session()


def rusetfs_funds(spec: dict) -> tuple:
    """Справочник фондов RusETFs; проверка здравости: ≥ min_rows фондов и ≥ 80 % с комиссией > 0."""
    rows = rusetfs.normalize(rusetfs.fetch_screener(session=_rusetfs_session()))
    _check("фондов RusETFs", len(rows), spec["min_rows"])
    # нулевая комиссия в санити не засчитывается: в живых данных минимум ~0,2 %, массовые нули — сбой
    with_fee = sum((r["commission_pct"] or 0) > 0 for r in rows)
    if with_fee < RUSETFS_MIN_COMMISSION_SHARE * len(rows):
        raise ValueError(f"комиссия > 0 есть у {with_fee} из {len(rows)} фондов — меньше "
                         f"{RUSETFS_MIN_COMMISSION_SHARE:.0%}, ответ похож на ошибку")
    return rows, len(rows), {"with_commission": with_fee}


def collect_rusetfs(name: str, spec: dict) -> dict:
    """Как collect_tinvest: атомарная запись, updated меняется только при изменении data;
    при сбое (исключение до _write) прежний файл не трогается."""
    data, n, extra = spec["collect"](spec)
    prev = _previous(name)
    updated = prev.get("updated") if prev.get("data") == data and prev.get("updated") else _now()
    _write(name, {"updated": updated, "source": RUSETFS_SOURCE, "source_url": spec["source_url"],
                  "data": data})
    return {"ok": True, "rows": n, "last": dt.date.today().isoformat(), **extra}


SOURCES["rusetfs_funds"] = {"kind": "rusetfs", "title": "Фонды: комиссии, УК, СЧА (RusETFs)",
                            "source_url": RUSETFS_URL, "collect": rusetfs_funds, "min_rows": 150}


# ------------------------------------------------------------------ рейтинги (ISS)
SYMBOL_STATS_SOURCE = "ISS MOEX (расчёт ИнвестАналитики)"
SYMBOL_STATS_BUDGET_MIN = 10.0     # бюджет времени источника, мин (шаг workflow «Рейтинги» — 15 мин)
SYMBOL_STATS_YEARS = 10            # окно — до 10 лет полных месяцев
SYMBOL_STATS_MIN_MONTHS = 36       # минимум месячных доходностей для попадания в рейтинг
_clock = time.monotonic            # часы бюджета (в тестах подменяются)


class _IssStatsLoader:
    """Доступ к ISS для symbol_stats: только через core/data/iss.py (кэш, ретраи)."""

    def __init__(self):
        self._funds: set[str] = set()

    def securities(self) -> pd.DataFrame:
        """Бумаги режима TQBR с колонками SECID, SECTYPE (акции и паи фондов)."""
        from core.data import iss
        df = iss.shares("TQBR")
        self._funds = set(df.loc[df["SECTYPE"].astype(str).isin(iss.FUND_TYPES), "SECID"])
        return df

    def monthly_closes(self, secid: str, start: str, end: str) -> pd.Series:
        """Месячные цены; свечи ISS уже скорректированы биржей на сплиты. Фонды — склейка TQTF
        (до июня 2026 г.) + TQBR."""
        from core.data import iss
        legacy = ("TQTF",) if secid in self._funds else ()
        return iss.monthly_closes(secid, start, end, board="TQBR", legacy_boards=legacy)


def _symbol_stats_loader():
    """Загрузчик ISS для symbol_stats (в тестах подменяется фейковым с теми же методами)."""
    return _IssStatsLoader()


def _key_rate_series() -> pd.Series:
    """Ключевая ставка ЦБ (доля годовых) из public/data/key_rate.json, собранного шагом «Сбор».
    Нет файла или он пуст — ошибка: рейтинги без безрисковой ставки не считаем."""
    rows = _previous("key_rate").get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("нет public/data/key_rate.json — ключевая ставка для rf недоступна")
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows if d and v is not None}, dtype=float)
    return s.sort_index()


def symbol_stats(spec: dict) -> tuple:
    """Коэффициенты и ранги акций (TQBR SECTYPE 1/2/D) и фондов (J/9/A/B) с историей ≥ 36 мес.
    Акции — по полной доходности (total_return_series с дивидендами dividends.json, item "tr": true),
    фонды — по ценам ("tr": false); нет dividends.json — акции тоже по ценам, "total_return": false.
    Сплиты: месячные свечи ISS уже скорректированы биржей задним числом — отдельной корректировки нет,
    "split_adjusted": true по источнику; дивиденды T-Invest — в текущих акциях.
    Месячные цены — свечи ISS interval=31 (один запрос на бумагу), окно — до 10 лет полных месяцев
    (текущий месяц не входит); rf — средняя по времени ключевая ставка за окно.
    Сбои по отдельным бумагам — пропуск; источник — сбой, если сбоев > MAX_FAIL_SHARE,
    MAX_CONSECUTIVE_FAILS подряд, исчерпан бюджет времени или акций в рейтинге < min_rows."""
    from core.analytics import ranking
    from core.data.iss import FUND_TYPES, SHARE_TYPES

    today = dt.date.today()
    till = today.replace(day=1) - dt.timedelta(days=1)                   # конец прошлого месяца
    first = (pd.Timestamp(till) - pd.DateOffset(months=12 * SYMBOL_STATS_YEARS)).to_period("M")
    start = first.to_timestamp().date()                                  # 1-е число месяца базовой цены
    rf = ranking.mean_rate(_key_rate_series(), first.to_timestamp("M"), till)
    if not math.isfinite(rf):
        raise ValueError("ключевая ставка за окно не определена")

    # полная доходность акций: дивиденды из dividends.json (T-Invest), собранного шагом «Сбор» или ранее;
    # нет файла — акции по ценам, "total_return": false (сайт покажет пометку)
    from core.analytics.metrics import total_return_series
    div_data = _previous("dividends").get("data")
    div_data = div_data if isinstance(div_data, dict) and div_data else None

    loader = _symbol_stats_loader()
    df = loader.securities()
    classes: dict[str, str] = {}
    for _, r in df.iterrows():
        sec, st = r.get("SECID"), str(r.get("SECTYPE"))
        if sec and st in SHARE_TYPES:
            classes[sec] = "share"
        elif sec and st in FUND_TYPES:
            classes[sec] = "fund"
    deadline = _clock() + spec.get("budget_min", SYMBOL_STATS_BUDGET_MIN) * 60
    items: dict[str, dict] = {}
    failed: list[str] = []
    short = streak = 0
    secs = sorted(classes)
    for i, sec in enumerate(secs):
        if _clock() > deadline:
            raise RuntimeError(f"исчерпан бюджет времени ({spec.get('budget_min', SYMBOL_STATS_BUDGET_MIN):g} мин): "
                               f"обработано {i} из {len(secs)} бумаг")
        try:
            s = loader.monthly_closes(sec, start.isoformat(), till.isoformat())
            streak = 0
        except Exception as e:  # noqa: BLE001
            failed.append(sec)
            streak += 1
            if len(failed) <= 20:
                print(f"  пропуск свечей {sec}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            if streak >= MAX_CONSECUTIVE_FAILS:
                raise RuntimeError(f"{streak} сбоев ISS подряд — источник прерван; последний: "
                                   f"{type(e).__name__}: {str(e)[:200]}") from None
            continue
        s = s.dropna()
        if s.empty:                          # нет свечей за окно (новая или неторгуемая бумага)
            short += 1
            continue
        s.index = pd.DatetimeIndex(s.index)
        s = s[(s.index >= first.to_timestamp("M")) & (s.index <= pd.Timestamp(till))]
        if len(s) - 1 < SYMBOL_STATS_MIN_MONTHS:
            short += 1
            continue
        tr = classes[sec] == "share" and div_data is not None
        if tr:
            s = total_return_series(s, ranking.ex_dividends(div_data.get(sec)))
        items[sec] = {"class": classes[sec], "tr": tr, **ranking.symbol_metrics(s, rf)}
    if len(failed) > 20:
        print(f"  … всего пропусков свечей: {len(failed)}", file=sys.stderr)
    if secs and len(failed) / len(secs) > MAX_FAIL_SHARE:
        raise RuntimeError(f"сбоев {len(failed)} из {len(secs)} бумаг — больше {MAX_FAIL_SHARE:.0%}")
    ranking.rank_items(items)
    n_cls = {c: sum(it["class"] == c for it in items.values()) for c in ("share", "fund")}
    _check("акций с историей ≥ 36 мес.", n_cls["share"], spec["min_rows"])
    body = {
        "total_return": div_data is not None,
        "split_adjusted": True,          # свечи ISS скорректированы на сплиты биржей (проверено 08.10.2026)
        "rf": round(rf, 6),
        "window": {"from": first.to_timestamp("M").date().isoformat(), "till": till.isoformat(),
                   "max_months": 12 * SYMBOL_STATS_YEARS, "min_months": SYMBOL_STATS_MIN_MONTHS},
        "classes": {c: {"n": n} for c, n in n_cls.items()},
        "items": dict(sorted(items.items())),
    }
    extra = {"shares": n_cls["share"], "funds": n_cls["fund"], "short_history": short, "failed": len(failed),
             "total_return": div_data is not None}
    return body, len(items), extra


def collect_symbol_stats(name: str, spec: dict) -> dict:
    """Атомарная запись symbol_stats.json; updated меняется только при изменении rf / window / classes / items;
    при сбое (исключение до _write) прежний файл не трогается. Котировки в файл не пишутся."""
    body, n, extra = spec["collect"](spec)
    prev = _previous(name)
    same = all(prev.get(k) == body[k] for k in body)
    updated = prev.get("updated") if same and prev.get("updated") else _now()
    _write(name, {"updated": updated, "source": SYMBOL_STATS_SOURCE, **body})
    return {"ok": True, "rows": n, "last": body["window"]["till"], **extra}


# kind "iss": собирается после RusETFs и T-Invest; default=False — только явно (--only symbol_stats),
# отдельным шагом workflow «Рейтинги»: полный прогон без аргументов его не запускает
SOURCES["symbol_stats"] = {"kind": "iss", "default": False,
                           "title": "Рейтинги риск/доходность (расчёт по ISS)",
                           "collect": symbol_stats, "min_rows": 100,
                           "budget_min": SYMBOL_STATS_BUDGET_MIN}


# ------------------------------------------------------------------ ряды ЦБ
def collect(name: str, spec: dict) -> dict:
    s: pd.Series = spec["load"]()
    if len(s) < spec["min_rows"]:
        raise ValueError(f"слишком мало строк: {len(s)} < {spec['min_rows']} — ответ похож на ошибку")
    payload = {
        "id": name, "title": spec["title"], "unit": spec["unit"], "source": spec["source"],
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "first": s.index.min().strftime("%Y-%m-%d"), "last": s.index.max().strftime("%Y-%m-%d"),
        "data": _rows(s, spec["digits"]),
    }
    _write(name, payload)
    return {"ok": True, "rows": len(s), "last": payload["last"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="собрать только указанные источники")
    ap.add_argument("--skip", nargs="*", default=[], metavar="ИСТОЧНИК",
                    help="не собирать указанные источники (применяется после --only)")
    ap.add_argument("--tinvest-budget", type=float, default=TINVEST_BUDGET_MIN, metavar="МИН",
                    help=f"бюджет времени на все источники T-Invest, мин (по умолчанию {TINVEST_BUDGET_MIN:g})")
    args = ap.parse_args(argv)
    if not args.tinvest_budget > 0:
        ap.error("--tinvest-budget должен быть положительным числом минут")
    OUT.mkdir(parents=True, exist_ok=True)

    status_path = OUT / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except ValueError:
        status = {}
    # без --only — все источники, кроме default=False (symbol_stats: отдельный шаг workflow «Рейтинги»)
    names = args.only or [n for n, s in SOURCES.items() if s.get("default", True)]
    unknown = [n for n in [*names, *args.skip] if n not in SOURCES]
    if unknown:
        ap.error(f"неизвестные источники: {', '.join(unknown)}; есть: {', '.join(SOURCES)}")
    names = [n for n in names if n not in set(args.skip)]
    # ЦБ — первым, затем RusETFs, T-Invest — последним: долгий или упавший T-Invest
    # не должен помешать сохранить остальное (сортировка устойчивая — порядок внутри группы прежний)
    is_ti = lambda n: SOURCES[n].get("kind") == "tinvest"   # noqa: E731
    order = {"rusetfs": 1, "tinvest": 2, "iss": 3}
    names = sorted(names, key=lambda n: order.get(SOURCES[n].get("kind"), 0))
    failed = skipped = ti_tried = ti_failed = 0
    ctx: dict = {"budget_min": args.tinvest_budget}   # общий клиент T-Invest и список акций на прогон
    for name in names:
        spec = SOURCES[name]
        try:
            if is_ti(name):
                res = collect_tinvest(name, spec, ctx)
            elif spec.get("kind") == "rusetfs":
                res = collect_rusetfs(name, spec)
            elif spec.get("kind") == "iss":
                res = collect_symbol_stats(name, spec)
            else:
                res = collect(name, spec)
            print(f"OK   {name}: {res['rows']} строк, до {res['last']}")
        except Skipped as e:
            skipped += 1
            res = {"ok": False, "skipped": True, "error": str(e)}
            print(f"SKIP {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            ti_failed += is_ti(name)
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500], "where": _where(e)}
            print(f"FAIL {name}: {res['error']}", file=sys.stderr)
            traceback.print_exc(limit=2)
        ti_tried += is_ti(name) and not res.get("skipped")
        res["checked"] = _now()
        prev = status.get(name, {})
        if not res["ok"] and prev.get("last"):
            res["last"] = prev["last"]          # последние успешные данные остаются на сайте
        status[name] = res
        # после каждого источника: если job убьют по таймауту, уже собранное попадёт в коммит
        _write("status", status, indent=1)
    rus = status.get("rusetfs_funds") if "rusetfs_funds" in names else None
    if rus is not None and not rus.get("ok"):
        # отдельного правила для RusETFs в коде выхода нет: его сбой — обычный сбой источника.
        # Если собран хотя бы один другой источник (напр. ЦБ) — код 0, сайт работает на прежнем
        # rusetfs_funds.json; если RusETFs — единственный реально собиравшийся (--only rusetfs_funds,
        # или ЦБ тоже упал, а T-Invest пропущен без токена) — код 1 по общему правилу ниже
        print("::warning::RusETFs: справочник фондов не обновлён — " + _short(rus.get("error", "")))
    if ti_tried and ti_failed == ti_tried:
        print("::warning::T-Invest: все источники недоступны — " + _ti_reason(status))
    # код 1, если не удалось собрать ничего из того, что пытались собрать, или токен задан,
    # но упали все источники T-Invest (GitHub пришлёт уведомление); пропуск без токена — не сбой
    attempted = len(names) - skipped
    return 1 if (attempted and failed == attempted) or (ti_tried and ti_failed == ti_tried) else 0


def _short(err: str, n: int = 200) -> str:
    """Краткая причина для ::warning:: — первая строка текста ошибки, не длиннее n символов."""
    line = (str(err).strip().splitlines() or ["причина неизвестна"])[0]
    return line[:n] or "причина неизвестна"


def _where(e: BaseException) -> str:
    """Место ошибки для status.json (логи Actions не всегда доступны): последние 3 кадра «файл:строка функция»
    в пределах репозитория, без значений переменных."""
    frames = [f for f in traceback.extract_tb(e.__traceback__) if str(ROOT) in f.filename] or \
        traceback.extract_tb(e.__traceback__)
    return " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(frames[-3:]))


def _ti_reason(status: dict) -> str:
    """Причина сбоя всех источников T-Invest для предупреждения в GitHub (по текстам ошибок)."""
    errs = " ".join(str(v.get("error", "")) for k, v in status.items()
                    if SOURCES.get(k, {}).get("kind") == "tinvest" and isinstance(v, dict))
    if "TLS" in errs or "SSL" in errs or "CERTIFICATE" in errs.upper():
        return "нет доверия к TLS-сертификату (корневой сертификат Минцифры, TINVEST_CA_BUNDLE)"
    if "HTTP 401" in errs or "HTTP 403" in errs or "UNAUTHENTICATED" in errs.upper():
        return "токен отклонён (проверьте секрет TINVEST_TOKEN)"
    return "сеть или сервис (подробности в public/data/status.json)"


if __name__ == "__main__":
    sys.exit(main())
