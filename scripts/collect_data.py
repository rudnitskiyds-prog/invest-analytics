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
    names = args.only or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        ap.error(f"неизвестные источники: {', '.join(unknown)}; есть: {', '.join(SOURCES)}")
    # ЦБ — первым: долгий или упавший T-Invest не должен помешать сохранить ряды ЦБ
    is_ti = lambda n: SOURCES[n].get("kind") == "tinvest"   # noqa: E731
    names = [n for n in names if not is_ti(n)] + [n for n in names if is_ti(n)]
    failed = skipped = ti_tried = ti_failed = 0
    ctx: dict = {"budget_min": args.tinvest_budget}   # общий клиент T-Invest и список акций на прогон
    for name in names:
        spec = SOURCES[name]
        try:
            if is_ti(name):
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
            ti_failed += is_ti(name)
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
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
    if ti_tried and ti_failed == ti_tried:
        print("::warning::T-Invest: все источники недоступны — " + _ti_reason(status))
    # код 1, если не удалось собрать ничего из того, что пытались собрать, или токен задан,
    # но упали все источники T-Invest (GitHub пришлёт уведомление); пропуск без токена — не сбой
    attempted = len(names) - skipped
    return 1 if (attempted and failed == attempted) or (ti_tried and ti_failed == ti_tried) else 0


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
