"""
Справочник биржевых фондов RusETFs (rusetfs.com): комиссия, управляющая компания, СЧА,
класс активов, статус торгов.

Источник — JSON-API скринера сайта (GET https://rusetfs.com/api/v1/screener, массив объектов
по фондам; используется с разрешения авторов сайта, ссылка на источник обязательна).
HTML не парсится. Вызывается только сборщиком scripts/collect_data.py раз в сутки; приложение
читает public/data/rusetfs_funds.json (core/data/public_data.py: fund_info(), etf_commissions()).

Поля ответа, которые используются (имена — как в API, включая опечатку commisionPercent):
    ticker, isin, shortDescription, commisionPercent (ДОЛЯ годовых: 0.005 = 0,5 %),
    tradingStartDate ("YYYY-MM-DD"), assetClass.name, assetSubClass.name, region.name,
    currency.name, issuer.name (УК), assetsUnderManagementByCurrency.RUB (СЧА, руб.),
    tradeStatus.name, tags.investmentPolicy.modelFollowing / .dividendPolicy.
Метрики сайта (sharpe, sortino, var5Percent, revenueByCurrency, …) не сохраняются:
коэффициенты эффективности платформа считает сама (core/analytics/metrics.py).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any, Optional

import requests

from core.config import RUSETFS_URL

USER_AGENT = "InvestAnalytics data collector (student project)"
TRADING = "Торгуется"
ACTIVE = "Активная"
MAX_COMMISSION_PCT = 10.0          # % годовых; больше — заведомо ошибка данных
EMPTY = {"", "nan", "-", "—", "none", "null"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# ключи записи normalize() — ровно в этом порядке
FIELDS = ["ticker", "isin", "name", "issuer", "asset_class", "asset_subclass", "region", "currency",
          "commission_pct", "aum_rub", "trading_start", "trade_status", "active_management",
          "dividend_policy"]

_sleep = time.sleep                # подменяется в тестах


class RusEtfsError(RuntimeError):
    """Ошибка получения справочника RusETFs: HTTP-ошибка, сбой сети после всех повторов,
    ответ не JSON или не список."""


# ------------------------------------------------------------------ загрузка
def fetch_screener(session: Any = None, url: str = RUSETFS_URL, timeout: float = 30,
                   retries: int = 3, backoff: float = 2.0) -> list[dict]:
    """GET скринера RusETFs -> JSON-массив фондов как есть.

    Повторы (до retries раз, пауза backoff * 2**попытка, не больше 60 с) — на сетевые ошибки,
    таймауты, HTTP 429 и 5xx. Прочие 4xx, ответ не JSON или не список — RusEtfsError сразу.
    Проверка TLS-сертификата не отключается (verify по умолчанию requests)."""
    s = session if session is not None else requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    n = max(0, int(retries))
    last = ""
    for attempt in range(n + 1):
        if attempt:
            _sleep(min(60.0, backoff * 2 ** (attempt - 1)))
        try:
            r = s.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.SSLError as e:      # повтор не поможет: проблема доверия к сертификату
            raise RusEtfsError(f"RusETFs: ошибка TLS: {str(e)[:200]}") from None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, OSError) as e:
            last = f"{type(e).__name__}: {str(e)[:200]}"
            continue
        code = int(getattr(r, "status_code", 0) or 0)
        if code == 429 or code >= 500:
            last = f"HTTP {code}"
            continue
        if code >= 400:
            raise RusEtfsError(f"RusETFs: HTTP {code}")
        try:
            data = r.json()
        except ValueError:
            raise RusEtfsError("RusETFs: ответ не JSON") from None
        if not isinstance(data, list):
            raise RusEtfsError(f"RusETFs: ожидался JSON-массив, получен {type(data).__name__}")
        return data
    raise RusEtfsError(f"RusETFs: нет ответа после {n + 1} попыток ({last})")


# ------------------------------------------------------------------ нормализация
def _str(x: Any) -> Optional[str]:
    """Строка без пробелов по краям; None, "NaN", "-", "" -> None. Словарь {name: …} -> name."""
    if isinstance(x, dict):
        x = x.get("name")
    if x is None or isinstance(x, (bool, dict, list)):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    s = str(x).strip()
    return None if s.lower() in EMPTY else s


def _num(x: Any) -> Optional[float]:
    """Конечное число (int/float или числовая строка) -> float, иначе None (bool — не число)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, str):
        if _str(x) is None:
            return None
        x = x.strip().replace(",", ".")
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _date(s: Optional[str]) -> Optional[str]:
    """"YYYY-MM-DD…" -> "YYYY-MM-DD", если это существующая дата (2020-13-45 — нет), иначе None."""
    if not s or not _DATE_RE.match(s):
        return None
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _commission_pct(rec: dict) -> Optional[float]:
    """commisionPercent (доля годовых, опечатка API) -> % годовых, round 4; вне [0, 10] -> None.
    Комиссия фонда — суммарное вознаграждение УК, СД и прочих (TER), % СЧА в год."""
    raw = rec.get("commisionPercent", rec.get("commissionPercent"))
    v = _num(raw)
    if v is None:
        return None
    pct = round(v * 100, 4)
    return pct if 0 <= pct <= MAX_COMMISSION_PCT else None


def _aum_rub(rec: dict) -> Optional[float]:
    v = _num(_get(rec, "assetsUnderManagementByCurrency", "RUB"))
    return v if v is not None and v >= 0 else None


def _record(rec: dict) -> Optional[dict]:
    ticker = _str(rec.get("ticker"))
    if ticker is None:
        return None
    start = _str(rec.get("tradingStartDate"))
    model = _str(_get(rec, "tags", "investmentPolicy", "modelFollowing"))
    return {
        "ticker": ticker.upper(),
        "isin": _str(rec.get("isin")),
        "name": _str(rec.get("shortDescription")),
        "issuer": _str(rec.get("issuer")),
        "asset_class": _str(rec.get("assetClass")),
        "asset_subclass": _str(rec.get("assetSubClass")),
        "region": _str(rec.get("region")),
        "currency": _str(rec.get("currency")),
        "commission_pct": _commission_pct(rec),
        "aum_rub": _aum_rub(rec),
        "trading_start": _date(start),
        "trade_status": _str(rec.get("tradeStatus")),
        "active_management": None if model is None else model == ACTIVE,
        "dividend_policy": _str(_get(rec, "tags", "investmentPolicy", "dividendPolicy")),
    }


def _better(a: dict, b: dict) -> dict:
    """Из двух записей с одним тикером: торгуемая, иначе с большим СЧА (при равенстве — первая)."""
    ta, tb = a["trade_status"] == TRADING, b["trade_status"] == TRADING
    if ta != tb:
        return a if ta else b
    aum = lambda r: -1.0 if r["aum_rub"] is None else r["aum_rub"]   # noqa: E731
    return b if aum(b) > aum(a) else a


def normalize(raw: list[dict]) -> list[dict]:
    """Ответ скринера -> записи фондов с ключами FIELDS, отсортированные по тикеру.

    Записи без тикера и не-словари отбрасываются; строки "NaN"/"-"/"" -> None;
    commission_pct — % годовых (= commisionPercent × 100, round 4), None при отсутствии,
    нечисловом значении или вне [0, 10]; aum_rub — СЧА в рублях (assetsUnderManagementByCurrency.RUB);
    active_management — modelFollowing == "Активная" (None, если поля нет).
    Дубли тикера: остаётся запись со статусом «Торгуется», иначе с большим СЧА."""
    by_ticker: dict[str, dict] = {}
    for rec in raw or []:
        if not isinstance(rec, dict):
            continue
        r = _record(rec)
        if r is None:
            continue
        t = r["ticker"]
        by_ticker[t] = _better(by_ticker[t], r) if t in by_ticker else r
    return [by_ticker[t] for t in sorted(by_ticker)]
