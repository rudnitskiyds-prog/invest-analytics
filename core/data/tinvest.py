"""
Клиент T-Invest API (Т-Банк), только чтение справочников сервиса инструментов.

Используется сборщиком scripts/collect_data.py для данных, которых нет в ISS Мосбиржи:
дивиденды, фундаментальные показатели (мультипликаторы), комиссии фондов, сектор акций.
Приложение (Streamlit) T-Invest напрямую не вызывает — читает файлы сборщика
в public/data/ (см. core/data/public_data.py).

Протокол: REST-шлюз gRPC, POST {base}/tinkoff.public.invest.api.contract.v1.<Service>/<Method>,
JSON в camelCase (контракты — instruments.proto, описание — openapi.yaml документации API).
Деньги и котировки — {units, nano}: значение = units + nano / 1e9 (units — строка int64).
Даты — ISO-строки UTC.

Лимит сервиса инструментов — 200 unary-запросов в минуту с одного адреса (limits.md);
клиент равномерно ограничивает частоту (по умолчанию ≤ 150 в минуту).

Токен берётся из аргумента или переменной окружения TINVEST_TOKEN и никогда не попадает
в логи, repr и тексты исключений.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time
from typing import Any, Iterable, Optional

import requests

from core.config import TINVEST_BASE

CONTRACT = "tinkoff.public.invest.api.contract.v1"
# только сервисы справочников и котировок: клиент не умеет торговать и не трогает счета
READ_ONLY_SERVICES = {"InstrumentsService", "MarketDataService"}
MSK = dt.timezone(dt.timedelta(hours=3))


class TInvestError(RuntimeError):
    pass


class TInvestClient:
    """Клиент REST T-Invest API.

    token        — токен (только чтение); по умолчанию из переменной окружения TINVEST_TOKEN;
    base         — адрес REST-шлюза;
    session      — requests.Session (или совместимый объект с .post) — для тестов и мок-сессий;
    max_per_min  — верхняя граница частоты запросов (равномерно: не чаще 60/max_per_min сек.).
    """

    def __init__(self, token: Optional[str] = None, base: str = TINVEST_BASE,
                 session: Any = None, max_per_min: int = 150, timeout: float = 30.0,
                 retries: int = 5, backoff: float = 1.0):
        tok = (token or os.environ.get("TINVEST_TOKEN") or "").strip()
        if not tok:
            raise TInvestError("нет токена TINVEST_TOKEN")
        self.__token = tok
        self.base = base.rstrip("/")
        self.session = session or requests.Session()
        self.min_interval = 60.0 / max(1, int(max_per_min))
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._sleep = time.sleep          # подменяются в тестах
        self._clock = time.monotonic

    def __repr__(self) -> str:  # токен не показываем
        return f"TInvestClient(base={self.base!r}, max_per_min={round(60 / self.min_interval)})"

    # ----------------------------------------------------------------- helpers
    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next_at - now
            if wait > 0:
                self._sleep(wait)
                now += wait
            self._next_at = now + self.min_interval

    def _clean(self, text: str) -> str:
        """Текст для исключения: без токена и не длиннее 300 символов."""
        return str(text).replace(self.__token, "***")[:300]

    def _url(self, method: str) -> str:
        service, _, name = method.rpartition("/")
        service = service or "InstrumentsService"
        if service not in READ_ONLY_SERVICES or not name:
            raise TInvestError(f"метод {method!r} не разрешён: клиент только на чтение")
        return f"{self.base}/{CONTRACT}.{service}/{name}"

    def _retry_wait(self, attempt: int, resp=None) -> float:
        if resp is not None:
            reset = resp.headers.get("x-ratelimit-reset") if hasattr(resp, "headers") else None
            try:
                if reset is not None:
                    return min(65.0, float(reset) + 0.5)
            except (TypeError, ValueError):
                pass
        return min(60.0, self.backoff * 2 ** attempt)

    # ----------------------------------------------------------------- API
    def call(self, method: str, body: Optional[dict] = None) -> dict:
        """POST метода (по умолчанию сервиса инструментов: "Shares", "GetDividends", …;
        другой сервис — "MarketDataService/GetCandles"). Повторы с экспоненциальной паузой
        на 429 / 5xx / сетевые ошибки; прочие 4xx — сразу TInvestError."""
        url = self._url(method)
        headers = {"Authorization": f"Bearer {self.__token}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        last = ""
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                r = self.session.post(url, json=body or {}, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {self._clean(e)}"
                if attempt < self.retries:
                    self._sleep(self._retry_wait(attempt))
                    continue
                break
            code = r.status_code
            if code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise TInvestError(f"{method}: ответ не JSON") from None
            msg = self._clean(_error_text(r))
            last = f"HTTP {code}: {msg}"
            if code == 429 or code >= 500:
                if attempt < self.retries:
                    self._sleep(self._retry_wait(attempt, r))
                    continue
                break
            raise TInvestError(f"{method}: {last}")
        raise TInvestError(f"{method}: не удалось после {self.retries + 1} попыток ({last})")


def _error_text(r) -> str:
    try:
        j = r.json()
        if isinstance(j, dict):
            return " ".join(str(j[k]) for k in ("code", "message", "description") if j.get(k) is not None)
    except ValueError:
        pass
    return (getattr(r, "text", "") or "")[:200]


# --------------------------------------------------------------------- converters
def quotation(v: Any) -> Optional[float]:
    """Quotation {units, nano} -> float: units + nano / 1e9. Нет поля -> None.
    Пустой объект {} — это ноль (proto3 JSON не передаёт нулевые поля)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, dict):
        return None
    try:
        return int(v.get("units") or 0) + int(v.get("nano") or 0) / 1e9
    except (TypeError, ValueError):
        return None


def money(v: Any) -> Optional[float]:
    """MoneyValue {currency, units, nano} -> float (валюта отбрасывается, см. currency_of)."""
    return quotation(v)


def currency_of(v: Any) -> Optional[str]:
    c = v.get("currency") if isinstance(v, dict) else None
    return c.upper() if c else None


def to_date(s: Any) -> Optional[str]:
    """ISO-время UTC -> 'YYYY-MM-DD' по московскому времени (полночь МСК приходит как 21:00Z
    предыдущего дня). Пустое значение и нулевая метка proto (1970-01-01) -> None."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    if t.year <= 1970:
        return None
    return t.astimezone(MSK).date().isoformat()


def _ts(d: Any) -> str:
    """Дата/строка -> Timestamp UTC для тела запроса."""
    return f"{str(d)[:10]}T00:00:00Z"


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _get(obj: dict, snake: str, default=None):
    """Поле ответа по имени proto: camelCase (REST по умолчанию) или snake_case."""
    if snake in obj:
        return obj[snake]
    return obj.get(_camel(snake), default)


# --------------------------------------------------------------------- methods
def shares(client: TInvestClient) -> list[dict]:
    """Акции основного режима TQBR (метод Shares, INSTRUMENT_STATUS_BASE)."""
    resp = client.call("Shares", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})
    out = []
    for x in resp.get("instruments", []):
        if _get(x, "class_code") != "TQBR":
            continue
        out.append({
            "ticker": _get(x, "ticker"), "class_code": "TQBR", "isin": _get(x, "isin"),
            "name": _get(x, "name"), "sector": _get(x, "sector") or None,
            "lot": int(_get(x, "lot") or 0) or None, "currency": (_get(x, "currency") or "").upper() or None,
            "uid": _get(x, "uid"), "asset_uid": _get(x, "asset_uid"),
            "div_yield_flag": bool(_get(x, "div_yield_flag", False)),
            "for_qual_investor_flag": bool(_get(x, "for_qual_investor_flag", False)),
            "liquidity_flag": bool(_get(x, "liquidity_flag", False)),
        })
    return out


def etfs(client: TInvestClient) -> list[dict]:
    """Фонды режимов TQBR и TQTF (метод Etfs). fixed_commission — как отдаёт API
    (по документации — «фиксированная комиссия за управление в процентах»), без пересчёта."""
    resp = client.call("Etfs", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})
    out = []
    for x in resp.get("instruments", []):
        cc = _get(x, "class_code")
        if cc not in ("TQBR", "TQTF"):
            continue
        out.append({
            "ticker": _get(x, "ticker"), "class_code": cc, "isin": _get(x, "isin"),
            "name": _get(x, "name"), "focus_type": _get(x, "focus_type") or None,
            "fixed_commission": quotation(_get(x, "fixed_commission")),
            "rebalancing_freq": _get(x, "rebalancing_freq") or None,
            "num_shares": quotation(_get(x, "num_shares")),
            "currency": (_get(x, "currency") or "").upper() or None,
            "uid": _get(x, "uid"), "asset_uid": _get(x, "asset_uid"),
        })
    return out


def dividends(client: TInvestClient, instrument_id: str, date_from: Any, date_to: Any) -> list[dict]:
    """Выплаты по бумаге (метод GetDividends, фильтр API — по record_date), по возрастанию
    record_date. value — на 1 бумагу (dividend_net), yield_value — в процентах, как отдаёт API.
    Отменённые (dividend_type == "Cancelled") остаются с cancelled = True."""
    resp = client.call("GetDividends", {"instrumentId": instrument_id,
                                        "from": _ts(date_from), "to": _ts(date_to)})
    out = []
    for x in resp.get("dividends", []):
        net = _get(x, "dividend_net")
        dtype = _get(x, "dividend_type") or None
        out.append({
            "record_date": to_date(_get(x, "record_date")),
            "last_buy_date": to_date(_get(x, "last_buy_date")),
            "payment_date": to_date(_get(x, "payment_date")),
            "declared_date": to_date(_get(x, "declared_date")),
            "value": money(net), "currency": currency_of(net),
            "dividend_type": dtype, "regularity": _get(x, "regularity") or None,
            "yield_value": quotation(_get(x, "yield_value")),
            "close_price": money(_get(x, "close_price")),
            "cancelled": dtype == "Cancelled",
        })
    out.sort(key=lambda r: (r["record_date"] is None, r["record_date"] or ""))
    return out


# Числовые поля GetAssetFundamentalsResponse.StatisticResponse (instruments.proto), имена proto
FUNDAMENTAL_NUMBERS = [
    "market_capitalization", "high_price_last_52_weeks", "low_price_last_52_weeks",
    "average_daily_volume_last_10_days", "average_daily_volume_last_4_weeks", "beta", "free_float",
    "forward_annual_dividend_yield", "shares_outstanding", "revenue_ttm", "ebitda_ttm",
    "net_income_ttm", "eps_ttm", "diluted_eps_ttm", "free_cash_flow_ttm",
    "five_year_annual_revenue_growth_rate", "three_year_annual_revenue_growth_rate", "pe_ratio_ttm",
    "price_to_sales_ttm", "price_to_book_ttm", "price_to_free_cash_flow_ttm",
    "total_enterprise_value_mrq", "ev_to_ebitda_mrq", "net_margin_mrq", "net_interest_margin_mrq",
    "roe", "roa", "roic", "total_debt_mrq", "total_debt_to_equity_mrq", "total_debt_to_ebitda_mrq",
    "free_cash_flow_to_price", "net_debt_to_ebitda", "current_ratio_mrq",
    "fixed_charge_coverage_ratio_fy", "dividend_yield_daily_ttm", "dividend_rate_ttm",
    "dividends_per_share", "five_years_average_dividend_yield", "five_year_annual_dividend_growth_rate",
    "dividend_payout_ratio_fy", "buy_back_ttm", "one_year_annual_revenue_growth_rate",
    "adr_to_common_share_ratio", "number_of_employees", "revenue_change_five_years",
    "eps_change_five_years", "ebitda_change_five_years", "total_debt_change_five_years", "ev_to_sales",
]
FUNDAMENTAL_STRINGS = ["asset_uid", "currency", "domicile_indicator_code"]
FUNDAMENTAL_DATES = ["ex_dividend_date", "fiscal_period_start_date", "fiscal_period_end_date"]
FUNDAMENTALS_BATCH = 100


def _num(v: Any) -> Optional[float]:
    """double из ответа: отсутствие поля и 0 -> None (документация API: «значение 0 в ответе
    следует приравнивать к отсутствию данных»)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f == 0 or f != f else f


def fundamentals(client: TInvestClient, asset_uids: Iterable[str]) -> dict[str, dict]:
    """Фундаментальные показатели по активам (метод GetAssetFundamentals, пачками ≤ 100).
    Ключи — asset_uid; поля — все поля StatisticResponse в snake_case, как в proto
    (числа — float или None, даты — 'YYYY-MM-DD'). Значения без пересчёта."""
    uids = list(dict.fromkeys(u for u in asset_uids if u))
    out: dict[str, dict] = {}
    for i in range(0, len(uids), FUNDAMENTALS_BATCH):
        resp = client.call("GetAssetFundamentals", {"assets": uids[i:i + FUNDAMENTALS_BATCH]})
        for x in resp.get("fundamentals", []):
            rec: dict[str, Any] = {k: _get(x, k) or None for k in FUNDAMENTAL_STRINGS}
            rec.update({k: _num(_get(x, k)) for k in FUNDAMENTAL_NUMBERS})
            rec.update({k: to_date(_get(x, k)) for k in FUNDAMENTAL_DATES})
            if rec["asset_uid"]:
                out[rec["asset_uid"]] = rec
    return out
