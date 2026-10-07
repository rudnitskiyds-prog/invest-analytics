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

Если источник не ответил, прежний файл остаётся нетронутым, а в status.json
записывается ошибка — сайт продолжает работать на последних успешных данных.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Callable

import pandas as pd

from core.config import ROOT
from core.data import cbr, tinvest

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


class Skipped(Exception):
    """Источник пропущен (нет токена) — не сбой."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _write(name: str, payload: dict) -> None:
    """Атомарная запись: при сбое посреди записи прежний файл не портится."""
    tmp = OUT / f".{name}.json.tmp"
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(OUT / f"{name}.json")
    except BaseException:
        tmp.unlink(missing_ok=True)      # не оставлять мусор: workflow делает git add public/data
        raise


def _client(ctx: dict) -> "tinvest.TInvestClient":
    if "client" not in ctx:
        if not os.environ.get("TINVEST_TOKEN", "").strip():
            raise Skipped("нет токена")
        ctx["client"] = tinvest.TInvestClient()
    return ctx["client"]


def _shares(ctx: dict) -> list[dict]:
    """Акции TQBR: из текущего прогона (источник tinvest_shares) или свежим запросом.
    Список, не прошедший проверку здравости, не используется и зависимыми источниками
    (dividends, fundamentals): они тоже получают сбой, прежние файлы остаются."""
    if "shares_error" in ctx:
        raise RuntimeError(f"список акций не прошёл проверку ({ctx['shares_error']})")
    if "shares" not in ctx:
        rows = tinvest.shares(_client(ctx))
        need = SOURCES.get("tinvest_shares", {}).get("min_rows", 150)
        if len(rows) < need:
            ctx["shares_error"] = f"акций {len(rows)} < {need}"
            raise ValueError(f"слишком мало акций: {len(rows)} < {need} — ответ похож на ошибку")
        ctx["shares"] = rows
    return ctx["shares"]


def _check(what: str, n: int, need: int) -> None:
    if n < need:
        raise ValueError(f"слишком мало {what}: {n} < {need} — ответ похож на ошибку")


def _tolerant(items: list, fn: Callable, label: Callable, what: str) -> list:
    """fn(item) по каждому элементу; ошибка элемента — лог и пропуск,
    но при доле сбоев > MAX_FAIL_SHARE источник считается сбойным."""
    res, errors = [], 0
    for it in items:
        try:
            res.append((it, fn(it)))
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors <= 20:             # не засоряем лог Actions при массовом сбое
                print(f"  пропуск {what} {label(it)}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
    if errors > 20:
        print(f"  … всего пропусков {what}: {errors}", file=sys.stderr)
    if items and errors / len(items) > MAX_FAIL_SHARE:
        raise RuntimeError(f"сбоев {errors} из {len(items)} ({what}) — больше {MAX_FAIL_SHARE:.0%}")
    return res


def tinvest_shares(ctx: dict, spec: dict) -> tuple[object, int]:
    rows = sorted(_shares(ctx), key=lambda r: r["ticker"] or "")
    _check("акций", len(rows), spec["min_rows"])          # порог _shares задан SOURCES
    return rows, len(rows)


def tinvest_etfs(ctx: dict, spec: dict) -> tuple[object, int]:
    rows = sorted(tinvest.etfs(_client(ctx)), key=lambda r: r["ticker"] or "")
    _check("фондов", len(rows), spec["min_rows"])
    return rows, len(rows)


def tinvest_dividends(ctx: dict, spec: dict) -> tuple[object, int]:
    client = _client(ctx)
    till = (dt.date.today() + dt.timedelta(days=365)).isoformat()
    items = [r for r in _shares(ctx) if r.get("uid") and r.get("ticker")]
    got = _tolerant(items, lambda r: tinvest.dividends(client, r["uid"], START, till),
                    lambda r: r["ticker"], "дивидендов")
    data: dict[str, list] = {}
    for share, rows in got:
        if rows:
            data.setdefault(share["ticker"], []).extend(rows)
    for t in data:
        data[t].sort(key=lambda r: (r["record_date"] is None, r["record_date"] or ""))
    _check("тикеров с дивидендами", len(data), spec["min_rows"])
    return dict(sorted(data.items())), len(data)


def tinvest_fundamentals(ctx: dict, spec: dict) -> tuple[object, int]:
    client = _client(ctx)
    by_asset: dict[str, list[str]] = {}
    for r in _shares(ctx):
        if r.get("asset_uid") and r.get("ticker"):
            by_asset.setdefault(r["asset_uid"], []).append(r["ticker"])
    uids = list(by_asset)
    n = tinvest.FUNDAMENTALS_BATCH
    batches = [uids[i:i + n] for i in range(0, len(uids), n)]
    got = _tolerant(batches, lambda b: tinvest.fundamentals(client, b),
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
    data, n = spec["collect"](ctx, spec)
    _write(name, {"updated": _now(), "source": TINVEST_SOURCE, "data": data})
    return {"ok": True, "rows": n, "last": dt.date.today().isoformat()}


# порядок важен: акции -> фонды -> дивиденды -> фундаментальные показатели
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
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)

    status_path = OUT / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    names = args.only or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        ap.error(f"неизвестные источники: {', '.join(unknown)}; есть: {', '.join(SOURCES)}")
    failed = skipped = 0
    ctx: dict = {}                       # общий клиент T-Invest и список акций на один прогон
    for name in names:
        spec = SOURCES[name]
        try:
            if spec.get("kind") == "tinvest":
                res = collect_tinvest(name, spec, ctx)
            else:
                res = collect(name, spec)
            print(f"OK   {name}: {res['rows']} строк, до {res['last']}")
        except Skipped as e:
            skipped += 1
            res = {"ok": False, "skipped": True, "error": str(e)}
            print(f"SKIP {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
            print(f"FAIL {name}: {res['error']}", file=sys.stderr)
            traceback.print_exc(limit=2)
        res["checked"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        prev = status.get(name, {})
        if not res["ok"] and prev.get("last"):
            res["last"] = prev["last"]          # последние успешные данные остаются на сайте
        status[name] = res
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    # код 1 только если не удалось собрать ничего из того, что пытались собрать, —
    # тогда GitHub пришлёт уведомление об ошибке; пропуск без токена сбоем не считается
    attempted = len(names) - skipped
    return 1 if attempted and failed == attempted else 0


if __name__ == "__main__":
    sys.exit(main())
