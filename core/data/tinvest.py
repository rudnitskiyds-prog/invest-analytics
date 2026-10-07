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

TLS: сертификат invest-public-api.tbank.ru выпущен удостоверяющим центром Минцифры
(Russian Trusted Root CA), которого нет в стандартном наборе certifi. Путь к набору корневых
сертификатов (certifi + сертификаты Минцифры) задаётся переменной TINVEST_CA_BUNDLE — его
собирает workflow сборщика. Проверку сертификата клиент не отключает никогда.
"""
from __future__ import annotations

import datetime as dt
import os
import re
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
    """Ошибка T-Invest API. transient=True — сбой сервиса или сети (429/5xx/таймаут после всех
    повторов, исчерпан бюджет времени), а не ответ про конкретную бумагу (прочие 4xx);
    budget=True — исчерпан бюджет времени клиента (deadline)."""

    def __init__(self, msg: str, transient: bool = False, budget: bool = False):
        super().__init__(msg)
        self.transient = transient or budget
        self.budget = budget


_TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")
_BEARER_RE = re.compile(r"Bearer\s+\S+")


class TInvestClient:
    """Клиент REST T-Invest API.

    token        — токен (только чтение); по умолчанию из переменной окружения TINVEST_TOKEN;
    base         — адрес REST-шлюза;
    session      — requests.Session (или совместимый объект с .post) — для тестов и мок-сессий;
    max_per_min  — верхняя граница частоты запросов (равномерно: не чаще 60/max_per_min сек.).

    Атрибут deadline (по часам клиента _clock, по умолчанию None) — бюджет времени: после него
    новые попытки не делаются и паузы повторов не превышают остаток (TInvestError, budget=True).
    ca_bundle    — путь к набору корневых сертификатов (по умолчанию TINVEST_CA_BUNDLE; нет — certifi).
    """

    def __init__(self, token: Optional[str] = None, base: str = TINVEST_BASE,
                 session: Any = None, max_per_min: int = 150, timeout: float = 30.0,
                 retries: int = 5, backoff: float = 1.0, ca_bundle: Optional[str] = None):
        tok = (token or os.environ.get("TINVEST_TOKEN") or "").strip()
        if not tok:
            raise TInvestError("нет токена TINVEST_TOKEN")
        if not _TOKEN_RE.match(tok):
            raise TInvestError("токен содержит недопустимые символы")
        self.__token = tok
        self.base = base.rstrip("/")
        self.session = session or requests.Session()
        self.verify = _ca_bundle(ca_bundle)
        self.min_interval = 60.0 / max(1, int(max_per_min))
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._sleep = time.sleep          # подменяются в тестах
        self._clock = time.monotonic
        self.deadline: Optional[float] = None

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
        """Текст для исключения: без токена (в т.ч. в repr-форме и после Bearer), ≤ 300 символов."""
        t = str(text).replace(self.__token, "***").replace(repr(self.__token), "***")
        return _BEARER_RE.sub("Bearer ***", t)[:300]

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
    def _left(self) -> Optional[float]:
        return None if self.deadline is None else self.deadline - self._clock()

    def call(self, method: str, body: Optional[dict] = None, retries: Optional[int] = None) -> dict:
        """POST метода (по умолчанию сервиса инструментов: "Shares", "GetDividends", …;
        другой сервис — "MarketDataService/GetCandles"). Повторы с экспоненциальной паузой
        на 429 / 5xx / сетевые ошибки (retries — число повторов, по умолчанию self.retries);
        прочие 4xx — сразу TInvestError."""
        url = self._url(method)
        n_retries = self.retries if retries is None else max(0, int(retries))
        headers = {"Authorization": f"Bearer {self.__token}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        last = ""
        for attempt in range(n_retries + 1):
            left = self._left()
            if left is not None and left <= 0:
                raise TInvestError(f"{method}: исчерпан бюджет времени ({last or 'запрос не начат'})",
                                   budget=True)
            self._throttle()
            try:
                extra = {} if self.verify is True else {"verify": self.verify}
                r = self.session.post(url, json=body or {}, headers=headers, timeout=self.timeout, **extra)
            except requests.exceptions.SSLError as e:
                # недоверенный сертификат повтором не лечится — сразу ошибка сервиса (не бумаги)
                raise TInvestError(f"{method}: TLS — {TLS_HINT} ({type(e).__name__}: {self._clean(e)})",
                                   transient=True) from None
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {self._clean(e)}"
                if attempt < n_retries:
                    self._pause(self._retry_wait(attempt))
                    continue
                break
            code = r.status_code
            if code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise TInvestError(f"{method}: ответ не JSON", transient=True) from None
            msg = self._clean(_error_text(r))
            last = f"HTTP {code}: {msg}"
            if code == 429 or code >= 500:
                if attempt < n_retries:
                    self._pause(self._retry_wait(attempt, r))
                    continue
                break
            raise TInvestError(f"{method}: {last}")
        raise TInvestError(f"{method}: не удалось после {n_retries + 1} попыток ({last})", transient=True)

    def _pause(self, wait: float) -> None:
        """Пауза перед повтором, но не дальше deadline (дальше call сам прервётся)."""
        left = self._left()
        self._sleep(wait if left is None else max(0.0, min(wait, left)))


TLS_HINT = ("нет доверия к сертификату сервера; нужен набор с корневым сертификатом Минцифры "
            "(переменная TINVEST_CA_BUNDLE)")


def _ca_bundle(path: Optional[str]) -> Any:
    """Значение verify для requests: путь к набору сертификатов или True (certifi)."""
    if path is False:
        raise TInvestError("проверку TLS-сертификата отключать нельзя")
    p = (path or os.environ.get("TINVEST_CA_BUNDLE") or "").strip()
    if not p:
        return True
    if not os.path.isfile(p):
        raise TInvestError(f"TINVEST_CA_BUNDLE: файл не найден ({p})")
    return p


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
        if _get(x, "class_code") != "TQBR" or not _get(x, "ticker"):
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
    return _one_per_ticker(out)


def _one_per_ticker(rows: list[dict]) -> list[dict]:
    """Один инструмент на тикер: предпочтительно liquidity_flag = True, иначе первый."""
    best: dict[str, dict] = {}
    for r in rows:
        t = r["ticker"]
        if t not in best or (r.get("liquidity_flag") and not best[t].get("liquidity_flag")):
            best[t] = r
    return list(best.values())


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


def dividends(client: TInvestClient, instrument_id: str, date_from: Any, date_to: Any,
              retries: Optional[int] = None) -> list[dict]:
    """Выплаты по бумаге (метод GetDividends, фильтр API — по record_date), по возрастанию
    record_date. value — на 1 бумагу (dividend_net), yield_value — в процентах, как отдаёт API.
    Отменённые (dividend_type == "Cancelled") остаются с cancelled = True.
    Дубли по (record_date, value, currency) схлопываются (действующая выплата важнее отменённой).
    retries — число повторов для этого вызова (сборщик ставит 2: вызовов много)."""
    resp = client.call("GetDividends", {"instrumentId": instrument_id,
                                        "from": _ts(date_from), "to": _ts(date_to)}, retries=retries)
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
    return dedup_dividends(out)


def dedup_dividends(rows: list[dict]) -> list[dict]:
    """Одна запись на (record_date, value, currency); при дубле действующая выплата важнее
    отменённой, иначе — первая. Результат — по возрастанию record_date."""
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = (r.get("record_date"), r.get("value"), r.get("currency"))
        if k not in seen or (seen[k].get("cancelled") and not r.get("cancelled")):
            seen[k] = r
    out = list(seen.values())
    out.sort(key=lambda r: (r.get("record_date") is None, r.get("record_date") or ""))
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
