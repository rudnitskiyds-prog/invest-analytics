"""Интеграция T-Invest API (только чтение): клиент, конвертеры, методы справочников,
источники сборщика, чтение public/data и витрина. Без сети: requests-сессия подменяется
фейком, ответы — из tests/fixtures/tinvest/ (структура openapi.yaml / instruments.proto,
данные выдуманные)."""
from __future__ import annotations

import copy
import json
import math
import pathlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from core import config
from core.analytics import screener
from core.data import iss, public_data, tinvest
from scripts import collect_data as cd

FIX = Path(__file__).parent / "fixtures" / "tinvest"
TOKEN = "t.FAKE-test-token-0000"           # фиктивный токен, нигде не действителен
PREFIX = f"{config.TINVEST_BASE}/{tinvest.CONTRACT}."
TINVEST_SOURCES = ["tinvest_shares", "tinvest_etfs", "dividends", "fundamentals"]


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ======================================================================= фейки
class FakeResp:
    def __init__(self, code: int, body=None, headers=None, text=None):
        self.status_code = code
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body, ensure_ascii=False)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Подмена requests.Session: запоминает вызовы, отвечает по сценарию.

    script — список ответов (FakeResp или исключение) по порядку, затем default(method, body)."""

    def __init__(self, default=None, script=None):
        self.calls: list[dict] = []
        self.script = list(script or [])
        self.default = default or (lambda method, body: FakeResp(200, {}))

    def post(self, url, json=None, headers=None, timeout=None):
        assert url.startswith(PREFIX), url
        service_method = url[len(PREFIX):]
        self.calls.append({"url": url, "method": service_method, "json": copy.deepcopy(json),
                           "headers": dict(headers or {}), "timeout": timeout})
        if self.script:
            item = self.script.pop(0)
        else:
            item = self.default(service_method, json)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    """Виртуальные часы: sleep сдвигает время, реального ожидания нет."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make_client(session, **kw) -> tuple[tinvest.TInvestClient, Clock]:
    c = tinvest.TInvestClient(token=TOKEN, session=session, **kw)
    clock = Clock()
    c._clock, c._sleep = clock, clock.sleep
    return c, clock


def ok(body):
    return FakeResp(200, body)


# ======================================================================= клиент
def test_no_token_raises(monkeypatch):
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    with pytest.raises(tinvest.TInvestError):
        tinvest.TInvestClient(session=FakeSession())
    with pytest.raises(tinvest.TInvestError):
        tinvest.TInvestClient(token="   ", session=FakeSession())
    monkeypatch.setenv("TINVEST_TOKEN", "  ")
    with pytest.raises(tinvest.TInvestError):
        tinvest.TInvestClient(session=FakeSession())


def test_authorization_header_url_and_body(monkeypatch):
    monkeypatch.setenv("TINVEST_TOKEN", f"  {TOKEN}\n")   # токен из окружения, пробелы обрезаются
    s = FakeSession(default=lambda m, b: ok({"instruments": []}))
    c = tinvest.TInvestClient(session=s)
    c._sleep = lambda _: None
    assert c.call("Shares", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}) == {"instruments": []}
    call = s.calls[0]
    assert call["url"] == f"{config.TINVEST_BASE}/tinkoff.public.invest.api.contract.v1.InstrumentsService/Shares"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["json"] == {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}
    assert call["timeout"] and call["timeout"] > 0
    # другой разрешённый сервис; пустое тело -> {}
    c.call("MarketDataService/GetCandles")
    assert s.calls[1]["url"].endswith(".MarketDataService/GetCandles")
    assert s.calls[1]["json"] == {}


@pytest.mark.parametrize("method", [
    "OrdersService/PostOrder", "OrdersService/CancelOrder", "StopOrdersService/PostStopOrder",
    "SandboxService/OpenSandboxAccount", "UsersService/GetAccounts", "OperationsService/GetPortfolio",
    "../OrdersService/PostOrder", "InstrumentsService/", "",
])
def test_trading_and_foreign_methods_refused_without_request(method):
    s = FakeSession()
    c, _ = make_client(s)
    with pytest.raises(tinvest.TInvestError):
        c.call(method, {})
    assert s.calls == []


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retry_on_429_and_5xx(code):
    err = FakeResp(code, {"code": 14, "message": "unavailable"})
    s = FakeSession(script=[err, err], default=lambda m, b: ok({"instruments": [{"ticker": "X"}]}))
    c, clock = make_client(s, backoff=1.0)
    assert c.call("Shares") == {"instruments": [{"ticker": "X"}]}
    assert len(s.calls) == 3
    # экспоненциальные паузы 1, 2 (без заголовка x-ratelimit-reset)
    assert [w for w in clock.sleeps if w >= 1] == [1.0, 2.0]


def test_retry_429_uses_ratelimit_reset_header():
    s = FakeSession(script=[FakeResp(429, {"code": 8, "message": "resource exhausted"},
                                     headers={"x-ratelimit-reset": "7"})],
                    default=lambda m, b: ok({}))
    c, clock = make_client(s, backoff=1.0)
    c.call("Etfs")
    assert len(s.calls) == 2
    assert 7.5 in clock.sleeps                       # reset + 0.5 с


def test_retry_on_connection_drop():
    s = FakeSession(script=[requests.ConnectionError("connection reset"), requests.Timeout("timed out")],
                    default=lambda m, b: ok({"dividends": []}))
    c, _ = make_client(s)
    assert c.call("GetDividends", {"instrumentId": "x"}) == {"dividends": []}
    assert len(s.calls) == 3


def test_retries_exhausted():
    s = FakeSession(default=lambda m, b: FakeResp(503, {"code": 14, "message": "unavailable"}))
    c, clock = make_client(s, retries=3, backoff=1.0)
    with pytest.raises(tinvest.TInvestError, match="503"):
        c.call("Shares")
    assert len(s.calls) == 4                          # 1 + 3 повтора
    assert [w for w in clock.sleeps if w >= 1] == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_no_retry_on_4xx(code):
    s = FakeSession(default=lambda m, b: FakeResp(code, {"code": 3, "message": "instrument not found",
                                                         "description": 50002}))
    c, clock = make_client(s)
    with pytest.raises(tinvest.TInvestError, match=str(code)):
        c.call("GetDividends", {"instrumentId": "nope"})
    assert len(s.calls) == 1
    assert all(w < 1 for w in clock.sleeps)


def test_non_json_200_is_error():
    s = FakeSession(default=lambda m, b: FakeResp(200, None, text="<html>proxy</html>"))
    c, _ = make_client(s)
    with pytest.raises(tinvest.TInvestError):
        c.call("Shares")


def _chain_text(e: BaseException) -> str:
    parts = []
    while e is not None:
        parts.append(f"{type(e).__name__}: {e} {e.args!r}")
        e = e.__cause__ or e.__context__
    return " | ".join(parts)


def test_token_not_leaked_in_repr_and_errors():
    # сервер эхом возвращает токен в тексте ошибки
    s = FakeSession(default=lambda m, b: FakeResp(401, {"code": 16, "message": f"bad token {TOKEN}"}))
    c, _ = make_client(s)
    assert TOKEN not in repr(c) and TOKEN not in str(c)
    with pytest.raises(tinvest.TInvestError) as ei:
        c.call("Shares")
    assert TOKEN not in _chain_text(ei.value)

    # 5xx с токеном в тексте до исчерпания повторов
    s = FakeSession(default=lambda m, b: FakeResp(503, None, text=f"upstream said Bearer {TOKEN}"))
    c, _ = make_client(s, retries=2)
    with pytest.raises(tinvest.TInvestError) as ei:
        c.call("Shares")
    assert TOKEN not in _chain_text(ei.value)

    # сетевая ошибка с токеном в сообщении
    s = FakeSession(default=lambda m, b: requests.ConnectionError(f"failed with header {TOKEN}"))
    c, _ = make_client(s, retries=1)
    with pytest.raises(tinvest.TInvestError) as ei:
        c.call("Shares")
    assert TOKEN not in _chain_text(ei.value)

    # нет токена: в тексте ничего лишнего, торговый метод — тоже без токена
    with pytest.raises(tinvest.TInvestError) as ei:
        c.call("OrdersService/PostOrder")
    assert TOKEN not in _chain_text(ei.value)


def test_rate_limit_virtual_clock():
    s = FakeSession(default=lambda m, b: ok({}))
    c, clock = make_client(s, max_per_min=120)            # не чаще 1 запроса в 0.5 с
    for _ in range(10):
        c.call("Shares")
    assert len(s.calls) == 10
    assert clock.sleeps == pytest.approx([0.5] * 9)
    assert clock.t - 1000.0 == pytest.approx(4.5)


def test_rate_limit_no_wait_when_time_passed():
    s = FakeSession(default=lambda m, b: ok({}))
    c, clock = make_client(s, max_per_min=150)            # 0.4 с
    c.call("Shares")
    clock.t += 0.1
    c.call("Shares")                                      # прошло 0.1 с -> ждать 0.3 с
    clock.t += 5
    c.call("Shares")                                      # прошло больше интервала -> не ждать
    assert clock.sleeps == pytest.approx([0.3])


def test_default_rate_within_service_limit():
    c = tinvest.TInvestClient(token=TOKEN, session=FakeSession())
    assert 60 / c.min_interval <= 200                     # лимит сервиса инструментов (limits.md)


# ======================================================================= конвертеры
@pytest.mark.parametrize("v, expected", [
    ({"units": "12", "nano": 340000000}, 12.34),
    ({"units": "-12", "nano": -500000000}, -12.5),
    ({"units": "0", "nano": -250000000}, -0.25),
    ({"nano": 950000000}, 0.95),
    ({"units": "9007199254"}, 9007199254.0),
    ({"units": 7, "nano": 0}, 7.0),
    ({}, 0.0),
])
def test_quotation_values(v, expected):
    assert tinvest.quotation(v) == pytest.approx(expected, abs=1e-12)


def test_quotation_missing_and_garbage():
    assert tinvest.quotation(None) is None
    assert tinvest.quotation("abc") is None
    assert tinvest.quotation({"units": "x1", "nano": 0}) is None


def test_money_and_currency():
    v = {"currency": "rub", "units": "-3", "nano": -50000000}
    assert tinvest.money(v) == pytest.approx(-3.05)
    assert tinvest.currency_of(v) == "RUB"
    assert tinvest.money({}) == 0.0
    assert tinvest.currency_of({}) is None
    assert tinvest.currency_of(None) is None
    assert tinvest.money(None) is None


@pytest.mark.parametrize("s, expected", [
    ("2025-07-17T21:00:00Z", "2025-07-18"),         # полночь МСК
    ("2025-07-17T20:59:59Z", "2025-07-17"),
    ("2025-07-18T00:00:00Z", "2025-07-18"),
    ("2025-12-31T21:00:00Z", "2026-01-01"),         # переход года
    ("2025-04-21T08:15:00.123456789Z", "2025-04-21"),  # наносекунды grpc-gateway
    ("2025-07-17T21:00:00+00:00", "2025-07-18"),
    ("1970-01-01T00:00:00Z", None),                 # нулевая метка proto
    ("", None), (None, None), ("not a date", None),
])
def test_to_date(s, expected):
    assert tinvest.to_date(s) == expected


# ======================================================================= методы
def test_shares_filters_tqbr_and_maps_fields():
    s = FakeSession(default=lambda m, b: ok(fixture("shares.json")))
    c, _ = make_client(s)
    rows = tinvest.shares(c)
    assert s.calls[0]["json"] == {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}
    assert [r["ticker"] for r in rows] == ["AAAA", "AAAAP", "BBBB", "CCCC"]
    assert {r["class_code"] for r in rows} == {"TQBR"}
    a = rows[0]
    assert a == {"ticker": "AAAA", "class_code": "TQBR", "isin": "RU000TEST001", "name": "Тестовая компания",
                 "sector": "financial", "lot": 10, "currency": "RUB", "uid": "uid-aaaa", "asset_uid": "asset-a",
                 "div_yield_flag": True, "for_qual_investor_flag": False, "liquidity_flag": True}
    cccc = rows[3]
    assert cccc["sector"] is None and cccc["lot"] is None and cccc["currency"] is None
    assert cccc["for_qual_investor_flag"] is True and cccc["div_yield_flag"] is False


def test_shares_accepts_snake_case_fields():
    body = {"instruments": [{"ticker": "SNAK", "class_code": "TQBR", "asset_uid": "asset-s", "uid": "u",
                             "lot": "100", "currency": "rub", "div_yield_flag": True}]}
    c, _ = make_client(FakeSession(default=lambda m, b: ok(body)))
    r = tinvest.shares(c)[0]
    assert (r["asset_uid"], r["lot"], r["div_yield_flag"]) == ("asset-s", 100, True)


def test_shares_empty_answer():
    c, _ = make_client(FakeSession(default=lambda m, b: ok({})))
    assert tinvest.shares(c) == []
    assert tinvest.etfs(c) == []


def test_etfs_filters_class_code_and_commission():
    c, _ = make_client(FakeSession(default=lambda m, b: ok(fixture("etfs.json"))))
    rows = tinvest.etfs(c)
    assert [(r["ticker"], r["class_code"]) for r in rows] == [("FNDA", "TQTF"), ("FNDB", "TQBR"), ("FNDC", "TQTF")]
    by = {r["ticker"]: r for r in rows}
    assert by["FNDA"]["fixed_commission"] == pytest.approx(0.95)
    assert by["FNDB"]["fixed_commission"] == pytest.approx(1.2)
    assert by["FNDB"]["num_shares"] == pytest.approx(250.5)
    assert by["FNDB"]["rebalancing_freq"] is None
    assert by["FNDC"]["fixed_commission"] is None            # поля нет в ответе
    assert by["FNDA"]["focus_type"] == "equity" and by["FNDA"]["currency"] == "RUB"


def test_dividends_fields_and_cancelled():
    s = FakeSession(default=lambda m, b: ok(fixture("dividends.json")))
    c, _ = make_client(s)
    rows = tinvest.dividends(c, "uid-aaaa", "2008-01-01", "2026-10-07")
    assert s.calls[0]["method"] == "InstrumentsService/GetDividends"
    assert s.calls[0]["json"] == {"instrumentId": "uid-aaaa", "from": "2008-01-01T00:00:00Z",
                                  "to": "2026-10-07T00:00:00Z"}
    # по возрастанию record_date
    assert [r["record_date"] for r in rows] == ["2022-06-10", "2024-01-12", "2025-07-18"]
    cancelled, semi, regular = rows
    assert cancelled["cancelled"] is True and cancelled["dividend_type"] == "Cancelled"
    assert cancelled["payment_date"] is None                   # 1970-01-01 -> None
    assert cancelled["yield_value"] == 0.0                     # {} -> 0
    assert semi["cancelled"] is False and semi["regularity"] == "Semi-Anl"
    assert regular == {
        "record_date": "2025-07-18", "last_buy_date": "2025-07-16", "payment_date": "2025-07-30",
        "declared_date": "2025-04-20", "value": pytest.approx(12.34), "currency": "RUB",
        "dividend_type": "Regular Cash", "regularity": "Annual", "yield_value": pytest.approx(8.23),
        "close_price": pytest.approx(150.0), "cancelled": False,
    }


def test_dividends_empty():
    c, _ = make_client(FakeSession(default=lambda m, b: ok({"dividends": []})))
    assert tinvest.dividends(c, "uid-x", "2008-01-01", "2026-01-01") == []
    c, _ = make_client(FakeSession(default=lambda m, b: ok({})))
    assert tinvest.dividends(c, "uid-x", "2008-01-01", "2026-01-01") == []


def _fund_session():
    tmpl = fixture("fundamentals.json")["fundamentals"][0]

    def answer(method, body):
        return ok({"fundamentals": [{**tmpl, "assetUid": a} for a in body["assets"]]})
    return FakeSession(default=answer)


@pytest.mark.parametrize("n, calls", [(0, 0), (1, 1), (100, 1), (101, 2), (250, 3)])
def test_fundamentals_batches(n, calls):
    s = _fund_session()
    c, _ = make_client(s)
    uids = [f"asset-{i}" for i in range(n)]
    out = tinvest.fundamentals(c, uids)
    assert len(s.calls) == calls == math.ceil(n / 100)
    assert all(len(call["json"]["assets"]) <= 100 for call in s.calls)
    assert [a for call in s.calls for a in call["json"]["assets"]] == uids
    assert set(out) == set(uids)


def test_fundamentals_dedup_and_skip_empty_uids():
    s = _fund_session()
    c, _ = make_client(s)
    out = tinvest.fundamentals(c, ["asset-a", "", None, "asset-a", "asset-b"])
    assert s.calls[0]["json"] == {"assets": ["asset-a", "asset-b"]}
    assert set(out) == {"asset-a", "asset-b"}


def test_fundamentals_values_zero_is_none():
    c, _ = make_client(FakeSession(default=lambda m, b: ok(fixture("fundamentals.json"))))
    out = tinvest.fundamentals(c, ["asset-a", "asset-b"])
    a, b = out["asset-a"], out["asset-b"]
    assert a["pe_ratio_ttm"] == 4.2 and a["price_to_book_ttm"] == 0.9 and a["roe"] == 22.5
    assert a["price_to_sales_ttm"] is None                      # 0 -> None
    assert a["market_capitalization"] == 5e11 and a["currency"] == "RUB"
    assert a["fiscal_period_end_date"] == "2025-12-31"          # 21:00Z -> следующий день МСК
    assert a["ex_dividend_date"] == "2025-07-16"
    assert a["ev_to_sales"] is None                             # поля нет в ответе
    assert b["market_capitalization"] is None                   # 0 -> None
    assert b["pe_ratio_ttm"] == -7.5 and b["roe"] == -4.0       # отрицательные сохраняются
    assert b["ex_dividend_date"] is None                        # 1970-01-01
    assert set(tinvest.FUNDAMENTAL_NUMBERS) <= set(a)


# ======================================================================= сборщик
class FakeTInvestAPI:
    """Фейковый REST T-Invest для сборщика: n_shares акций TQBR (пары с общим asset_uid),
    n_etfs фондов, дивиденды из фикстуры, фундаментальные показатели по шаблону."""

    def __init__(self, n_shares=160, n_etfs=60, bad_div=(), fail_fund=False):
        self.n_shares, self.n_etfs = n_shares, n_etfs
        self.bad_div = set(bad_div)
        self.fail_fund = fail_fund
        self.share_tmpl = fixture("shares.json")["instruments"][0]
        self.etf_tmpl = fixture("etfs.json")["instruments"][0]
        self.divs = fixture("dividends.json")
        self.fund_tmpl = fixture("fundamentals.json")["fundamentals"][0]

    def __call__(self, method, body):
        name = method.split("/")[-1]
        if name == "Shares":
            ins = [{**self.share_tmpl, "ticker": f"T{i:03d}", "uid": f"uid-{i}", "assetUid": f"asset-{i // 2}",
                    "sector": ["financial", "energy"][i % 2]} for i in range(self.n_shares)]
            ins.append({"ticker": "FRGN", "classCode": "SPBXM", "uid": "uid-f", "assetUid": "asset-f"})
            return ok({"instruments": ins})
        if name == "Etfs":
            return ok({"instruments": [{**self.etf_tmpl, "ticker": f"F{i:03d}", "uid": f"uid-e{i}"}
                                       for i in range(self.n_etfs)]})
        if name == "GetDividends":
            if body["instrumentId"] in self.bad_div:
                return FakeResp(400, {"code": 3, "message": "instrument not found", "description": 50002})
            return ok(self.divs)
        if name == "GetAssetFundamentals":
            if self.fail_fund:
                return FakeResp(400, {"code": 3, "message": "bad request"})
            # для asset-1 — пустая запись (все нули): данных нет
            return ok({"fundamentals": [
                {"assetUid": a, "currency": "RUB"} if a == "asset-1" else {**self.fund_tmpl, "assetUid": a}
                for a in body["assets"]]})
        raise AssertionError(method)


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Сборщик пишет в tmp_path; клиент T-Invest получает фейковую сессию и виртуальные часы."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setenv("TINVEST_TOKEN", TOKEN)
    state = {"api": FakeTInvestAPI(), "sessions": []}
    orig = tinvest.TInvestClient

    def factory(*a, **k):
        s = FakeSession(default=lambda m, b: state["api"](m, b))
        state["sessions"].append(s)
        c = orig(*a, session=s, **k)
        clock = Clock()
        c._clock, c._sleep = clock, clock.sleep
        return c
    monkeypatch.setattr(tinvest, "TInvestClient", factory)
    state["out"] = tmp_path
    return state


def _status(out: Path) -> dict:
    return json.loads((out / "status.json").read_text(encoding="utf-8"))


def _read(out: Path, name: str) -> dict:
    return json.loads((out / f"{name}.json").read_text(encoding="utf-8"))


def test_collector_tinvest_happy_path(collector):
    out = collector["out"]
    assert cd.main(["--only", *TINVEST_SOURCES]) == 0
    st = _status(out)
    assert all(st[n]["ok"] for n in TINVEST_SOURCES), st
    sh = _read(out, "tinvest_shares")
    assert sh["source"] == "T-Invest API" and "updated" in sh
    assert len(sh["data"]) == 160 and all(r["class_code"] == "TQBR" for r in sh["data"])
    assert [r["ticker"] for r in sh["data"]] == sorted(r["ticker"] for r in sh["data"])
    assert len(_read(out, "tinvest_etfs")["data"]) == 60
    dv = _read(out, "dividends")["data"]
    assert len(dv) == 160 and [r["record_date"] for r in dv["T000"]] == ["2022-06-10", "2024-01-12", "2025-07-18"]
    # один клиент на прогон, Shares запрашивается один раз
    assert len(collector["sessions"]) == 1
    methods = [c["method"].split("/")[-1] for c in collector["sessions"][0].calls]
    assert methods.count("Shares") == 1 and methods.count("GetDividends") == 160
    assert methods.count("GetAssetFundamentals") == 1           # 80 активов -> 1 пачка
    # токен не попал ни в один файл
    for f in out.iterdir():
        assert TOKEN not in f.read_text(encoding="utf-8")


def test_collector_shared_asset_uid_same_record(collector):
    out = collector["out"]
    assert cd.main(["--only", "tinvest_shares", "fundamentals"]) == 0
    f = _read(out, "fundamentals")["data"]
    # T000 и T001 — один актив asset-0
    assert f["T000"] == f["T001"]
    assert f["T000"]["pe"] == 4.2 and f["T000"]["roe"] == 22.5 and f["T000"]["ps"] is None
    assert f["T000"]["fiscal_period_end"] == "2025-12-31"
    assert set(f["T000"]) == set(cd.FUNDAMENTAL_FIELDS)
    # asset-1 (T002, T003) без данных -> записи нет
    assert "T002" not in f and "T003" not in f
    assert len(f) == 158


def test_collector_without_token_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)

    def no_network(*a, **k):
        raise AssertionError("без токена клиент не создаётся")
    monkeypatch.setattr(tinvest, "TInvestClient", no_network)
    (tmp_path / "status.json").write_text(json.dumps({"dividends": {"ok": True, "last": "2026-01-01"}}))
    assert cd.main(["--only", *TINVEST_SOURCES]) == 0           # пропуск — не сбой
    st = _status(tmp_path)
    for n in TINVEST_SOURCES:
        assert st[n]["ok"] is False and st[n]["skipped"] is True
        assert not (tmp_path / f"{n}.json").exists()
    assert st["dividends"]["last"] == "2026-01-01"


def test_collector_without_token_exit_code_with_cbr(tmp_path, monkeypatch):
    """Без токена: ЦБ собран — код 0; ЦБ упал — код 1 (из-за ЦБ, а не из-за пропусков)."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    good = pd.Series(range(3500), index=pd.bdate_range("2010-01-01", periods=3500), dtype=float)
    sources = {k: v for k, v in cd.SOURCES.items() if v.get("kind") == "tinvest"}
    sources["cbr_x"] = {**cd.SOURCES["cbr_gold"], "load": lambda: good}
    monkeypatch.setattr(cd, "SOURCES", sources)
    assert cd.main([]) == 0
    st = _status(tmp_path)
    assert st["cbr_x"]["ok"] and all(st[n].get("skipped") for n in TINVEST_SOURCES)

    def boom():
        raise ConnectionError("ЦБ недоступен")
    sources["cbr_x"] = {**sources["cbr_x"], "load": boom}
    assert cd.main([]) == 1


def test_collector_unknown_source_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    with pytest.raises(SystemExit) as ei:
        cd.main(["--only", "no_such_source"])
    assert ei.value.code == 2


def test_collector_too_few_shares_keeps_old_files(collector):
    out = collector["out"]
    old = {n: json.dumps({"updated": "2026-01-01T00:00:00+00:00", "source": "T-Invest API", "data": n})
           for n in TINVEST_SOURCES}
    for n, text in old.items():
        (out / f"{n}.json").write_text(text, encoding="utf-8")
    (out / "status.json").write_text(json.dumps({n: {"ok": True, "last": "2026-01-01"} for n in TINVEST_SOURCES}))
    collector["api"] = FakeTInvestAPI(n_shares=10, n_etfs=49)   # ниже порогов 150 / 50
    assert cd.main(["--only", *TINVEST_SOURCES]) == 1
    st = _status(out)
    for n in TINVEST_SOURCES:
        assert st[n]["ok"] is False and "skipped" not in st[n]
        assert st[n]["last"] == "2026-01-01"
        assert (out / f"{n}.json").read_text(encoding="utf-8") == old[n]
    assert "мало" in st["tinvest_shares"]["error"]


def test_collector_suspicious_shares_list_not_used_downstream(collector):
    """Список акций не прошёл проверку здравости (120 < 150, «ответ похож на ошибку») —
    дивиденды и показатели не должны перезаписываться по этому урезанному списку."""
    out = collector["out"]
    for n in ("dividends", "fundamentals"):
        (out / f"{n}.json").write_text('{"data": "old"}', encoding="utf-8")
    collector["api"] = FakeTInvestAPI(n_shares=120)
    cd.main(["--only", "tinvest_shares", "dividends", "fundamentals"])
    st = _status(out)
    assert st["tinvest_shares"]["ok"] is False
    for n in ("dividends", "fundamentals"):
        assert (out / f"{n}.json").read_text(encoding="utf-8") == '{"data": "old"}', n
        assert st[n]["ok"] is False, n


def test_collector_shares_threshold_boundary(collector):
    collector["api"] = FakeTInvestAPI(n_shares=150)
    assert cd.main(["--only", "tinvest_shares"]) == 0
    assert _status(collector["out"])["tinvest_shares"]["rows"] == 150


def test_collector_dividend_failures_20_percent_ok(collector):
    # 32 из 160 = 20 % — ещё допустимо (порог «больше 20 %»)
    collector["api"] = FakeTInvestAPI(bad_div={f"uid-{i}" for i in range(32)})
    assert cd.main(["--only", "dividends"]) == 0
    dv = _read(collector["out"], "dividends")["data"]
    assert len(dv) == 128 and "T000" not in dv and "T032" in dv


def test_collector_dividend_failures_over_20_percent_fail(collector):
    out = collector["out"]
    (out / "dividends.json").write_text('{"data": "old"}', encoding="utf-8")
    collector["api"] = FakeTInvestAPI(bad_div={f"uid-{i}" for i in range(33)})   # 33/160 > 20 %
    assert cd.main(["--only", "dividends"]) == 1
    st = _status(out)
    assert st["dividends"]["ok"] is False and "сбоев 33 из 160" in st["dividends"]["error"]
    assert (out / "dividends.json").read_text(encoding="utf-8") == '{"data": "old"}'


def test_collector_fundamentals_batch_failure(collector):
    collector["api"] = FakeTInvestAPI(fail_fund=True)
    assert cd.main(["--only", "fundamentals"]) == 1
    assert not (collector["out"] / "fundamentals.json").exists()


def test_write_is_atomic(tmp_path, monkeypatch):
    """Сбой посреди записи не портит прежний файл."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    target = tmp_path / "x.json"
    target.write_text('{"data": "old"}', encoding="utf-8")
    real_write = pathlib.Path.write_text

    def half_write(self, data, *a, **k):
        real_write(self, data[: len(data) // 2], *a, **k)
        raise OSError("диск переполнен")
    monkeypatch.setattr(pathlib.Path, "write_text", half_write)
    with pytest.raises(OSError):
        cd._write("x", {"data": "new" * 100})
    monkeypatch.setattr(pathlib.Path, "write_text", real_write)
    assert target.read_text(encoding="utf-8") == '{"data": "old"}'
    # успешная запись заменяет файл целиком и не оставляет временных файлов
    cd._write("x", {"data": [1, 2]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"data": [1, 2]}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    """Временный файл после сбоя не должен оставаться в public/data: workflow делает
    `git add public/data` и закоммитит мусор."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    real_write = pathlib.Path.write_text

    def half_write(self, data, *a, **k):
        real_write(self, data[:5], *a, **k)
        raise OSError("диск переполнен")
    monkeypatch.setattr(pathlib.Path, "write_text", half_write)
    with pytest.raises(OSError):
        cd._write("x", {"data": "new"})
    monkeypatch.setattr(pathlib.Path, "write_text", real_write)
    assert list(tmp_path.iterdir()) == []


# ======================================================================= public_data
@pytest.fixture
def pub(tmp_path, monkeypatch):
    """Каталог public/data для приложения — пустой tmp; файлы пишет тест."""
    d = tmp_path / "public"
    d.mkdir()
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", d)

    def put(name, data):
        (d / f"{name}.json").write_text(json.dumps({"updated": "2026-10-01T00:00:00+00:00",
                                                    "source": "T-Invest API", "data": data},
                                                   ensure_ascii=False), encoding="utf-8")
    return put


def test_public_data_dir_env_override(tmp_path):
    code = "from core import config; print(config.PUBLIC_DATA_DIR)"
    env = {**__import__("os").environ, "IP_PUBLIC_DATA_DIR": str(tmp_path)}
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=Path(__file__).resolve().parent.parent, check=True)
    assert res.stdout.strip() == str(tmp_path)


def test_public_data_missing_and_broken_files(pub, tmp_path):
    assert public_data.load("dividends") is None
    assert public_data.dividends("AAAA").empty
    assert list(public_data.dividends("AAAA").columns) == ["secid", "registryclosedate", "value", "currencyid"]
    assert public_data.fundamentals().empty
    assert public_data.share_sectors().empty
    assert public_data.etf_commissions().empty
    for n in ("dividends", "fundamentals", "tinvest_shares", "tinvest_etfs"):
        (config.PUBLIC_DATA_DIR / f"{n}.json").write_text("{не json", encoding="utf-8")
    assert public_data.load("dividends") is None
    assert public_data.dividends("AAAA").empty and public_data.fundamentals().empty
    assert public_data.share_sectors().empty and public_data.etf_commissions().empty


def test_public_data_dividends_excludes_cancelled(pub):
    pub("dividends", {"AAAA": [
        {"record_date": "2022-06-10", "value": 40.0, "currency": "RUB", "cancelled": True},
        {"record_date": "2024-01-12", "value": 5.5, "currency": "RUB", "cancelled": False},
        {"record_date": None, "value": 1.0, "currency": "RUB", "cancelled": False},
        {"record_date": "2025-07-18", "value": None, "currency": "RUB", "cancelled": False},
        {"record_date": "2025-07-18", "value": 12.34, "currency": None, "cancelled": False},
    ]})
    df = public_data.dividends("aaaa")
    assert df.to_dict("records") == [
        {"secid": "AAAA", "registryclosedate": "2024-01-12", "value": 5.5, "currencyid": "RUB"},
        {"secid": "AAAA", "registryclosedate": "2025-07-18", "value": 12.34, "currencyid": "RUB"},
    ]
    assert public_data.dividends("NONE").empty


def test_public_data_sectors_and_commissions(pub):
    pub("tinvest_shares", [{"ticker": "AAAA", "sector": "financial"}, {"ticker": "BBBB", "sector": "new_sector"},
                           {"ticker": "CCCC", "sector": None}])
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}, {"ticker": "FNDC", "fixed_commission": None}])
    sec = public_data.share_sectors()
    assert sec.to_dict() == {"AAAA": "Финансы", "BBBB": "new_sector"}
    fees = public_data.etf_commissions()
    assert fees["FNDA"] == 0.95 and np.isnan(fees["FNDC"])


# ----------------------------------------------------------------------- iss.dividends
def _iss_no_dividends(monkeypatch):
    calls = []

    def fake(path, params=None, ttl=None, retries=3):
        calls.append(path)
        return {"dividends": {"columns": ["secid", "isin", "registryclosedate", "value", "currencyid"], "data": []}}
    monkeypatch.setattr(iss, "get_json", fake)
    return calls


def test_iss_dividends_json_then_csv(pub, tmp_path, monkeypatch):
    _iss_no_dividends(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    (data_dir / "dividends.csv").write_text(
        "secid,registryclosedate,value\nAAAA,2020-01-01,99\nBBBB,2024-05-01,7.5\n", encoding="utf-8")
    pub("dividends", {"AAAA": [
        {"record_date": "2025-07-18", "value": 12.34, "currency": "RUB", "cancelled": False},
        {"record_date": "2022-06-10", "value": 40.0, "currency": "RUB", "cancelled": True},
        {"record_date": "2024-01-12", "value": 5.5, "currency": "RUB", "cancelled": False},
    ], "CANC": [{"record_date": "2024-01-12", "value": 1.0, "currency": "RUB", "cancelled": True}]})
    # есть в JSON -> JSON (CSV не используется), отменённая выплата исключена, сортировка по дате
    a = iss.dividends("AAAA")
    assert list(a["registryclosedate"]) == [pd.Timestamp("2024-01-12"), pd.Timestamp("2025-07-18")]
    assert list(a["value"]) == [5.5, 12.34]
    # нет в JSON -> CSV
    b = iss.dividends("BBBB")
    assert list(b["value"]) == [7.5] and list(b["currencyid"]) == ["RUB"]
    # в JSON только отменённые -> пусто в JSON -> CSV (там тоже нет) -> пусто
    assert iss.dividends("CANC").empty


def test_iss_dividends_iss_block_wins(pub, monkeypatch):
    monkeypatch.setattr(iss, "get_json", lambda *a, **k: {"dividends": {
        "columns": ["secid", "isin", "registryclosedate", "value", "currencyid"],
        "data": [["AAAA", "RU000TEST001", "2023-07-01", 3.0, "RUB"]]}})
    pub("dividends", {"AAAA": [{"record_date": "2025-07-18", "value": 12.34, "currency": "RUB"}]})
    assert list(iss.dividends("AAAA")["value"]) == [3.0]


def test_iss_dividends_no_files_no_iss_block(pub, tmp_path, monkeypatch):
    _iss_no_dividends(monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nothing")
    assert iss.dividends("AAAA").empty


def test_iss_dividends_iss_unavailable_uses_collector_file(pub, tmp_path, monkeypatch):
    """ISS недоступен (сеть/лимит), а файл сборщика лежит локально — выплаты должны браться из него."""
    def down(*a, **k):
        raise iss.ISSError("ISS недоступен")
    monkeypatch.setattr(iss, "get_json", down)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nothing")
    pub("dividends", {"AAAA": [{"record_date": "2025-07-18", "value": 12.34, "currency": "RUB"}]})
    assert list(iss.dividends("AAAA")["value"]) == [12.34]


# ----------------------------------------------------------------------- dividend_yield_12m
def test_dividend_yield_12m_ignores_future_and_cancelled(pub, tmp_path, monkeypatch):
    _iss_no_dividends(monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nothing")
    today = pd.Timestamp.today().normalize()
    d = lambda days: (today + pd.Timedelta(days=days)).date().isoformat()   # noqa: E731
    pub("dividends", {"AAAA": [
        {"record_date": d(-400), "value": 9.0, "cancelled": False},   # старше 12 мес.
        {"record_date": d(-365), "value": 1.0, "cancelled": False},   # граница — учитывается
        {"record_date": d(-100), "value": 3.0, "cancelled": False},
        {"record_date": d(-30), "value": 50.0, "cancelled": True},    # отменена
        {"record_date": d(0), "value": 2.0, "cancelled": False},      # реестр сегодня
        {"record_date": d(30), "value": 7.0, "cancelled": False},     # объявленная будущая
    ], "BBBB": [{"record_date": d(10), "value": 7.0, "cancelled": False}]})
    # 1 + 3 + 2 = 6 руб. на цену 100
    assert screener.dividend_yield_12m("AAAA", 100.0) == pytest.approx(0.06)
    # только будущая выплата -> 0, а не её доходность
    assert screener.dividend_yield_12m("BBBB", 100.0) == 0.0
    # нет данных или нет цены -> NaN
    assert np.isnan(screener.dividend_yield_12m("NONE", 100.0))
    assert np.isnan(screener.dividend_yield_12m("AAAA", 0.0))


# ----------------------------------------------------------------------- витрина
def _fake_market(monkeypatch, tmp_path):
    stocks = pd.DataFrame({
        "SECID": ["AAAA", "AAAAP", "BBBB"], "SHORTNAME": ["Тест", "Тест ап", "Второй"],
        "PRICE": [150.0, 140.0, 50.0], "LASTTOPREVPRICE": [1.0, -0.5, 0.0],
        "VALTODAY_RUR": [3e9, 2e9, 1e9], "CAP": [500e9, 100e9, 100e9], "LISTLEVEL": [1, 1, 2]})
    etfs = pd.DataFrame({
        "SECID": ["FNDA", "FNDX"], "SHORTNAME": ["Фонд А", "Фонд Х"], "FUNDTYPE": ["БПИФ", "БПИФ"],
        "PRICE": [10.0, 1.0], "LASTTOPREVPRICE": [0.1, 0.2], "VALTODAY_RUR": [1e8, 1e7]})
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    px = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    monkeypatch.setattr(iss, "stocks", lambda board="TQBR": stocks.copy())
    monkeypatch.setattr(iss, "etfs", lambda: etfs.copy())
    monkeypatch.setattr(iss, "close_series", lambda secid, start="2000-01-01", end=None, **k: px)
    monkeypatch.setattr(iss, "dividends", lambda secid: pd.DataFrame())
    monkeypatch.setattr(screener, "FUNDAMENTALS_CSV", tmp_path / "no_fundamentals.csv")


def test_showcase_without_tinvest_files(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    df = screener.shares_showcase(with_dividends=True)
    assert list(df["SECID"]) == ["AAAA", "AAAAP", "BBBB"]
    assert "Сектор" not in df and "P/E" not in df
    et = screener.etf_showcase()
    assert "Комиссия, %" not in et and list(et["SECID"]) == ["FNDA", "FNDX"]


def test_showcase_broken_tinvest_files(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    for n in ("fundamentals", "tinvest_shares", "tinvest_etfs"):
        (config.PUBLIC_DATA_DIR / f"{n}.json").write_text("[обрыв", encoding="utf-8")
    df = screener.shares_showcase(with_dividends=False)
    assert len(df) == 3 and "Сектор" not in df
    assert "Комиссия, %" not in screener.etf_showcase()


def test_showcase_with_tinvest_files(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    # CSV: AAAA (дублирует T-Invest) и BBBB (только здесь); млрд руб.
    csv = tmp_path / "fundamentals.csv"
    csv.write_text("secid,period,net_income,revenue,ebitda,equity,net_debt,shares_out,source\n"
                   "BBBB,2025,10,100,20,50,10,1,test\n"
                   "AAAA,2025,50,250,100,500,0,1,test\n", encoding="utf-8")
    monkeypatch.setattr(screener, "FUNDAMENTALS_CSV", csv)
    rec = {"pe": 4.2, "pb": 0.9, "ps": None, "ev_ebitda": 3.3, "nd_ebitda": 1.1, "roe": 22.5, "currency": "RUB"}
    pub("fundamentals", {"AAAA": rec, "AAAAP": rec, "ZZZZ": {**rec, "pe": 99.0}})
    pub("tinvest_shares", [{"ticker": "AAAA", "sector": "financial"}, {"ticker": "AAAAP", "sector": "financial"},
                           {"ticker": "BBBB", "sector": "energy"}])
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}])
    df = screener.shares_showcase(with_dividends=False).set_index("SECID")
    cols = list(df.columns)
    assert cols[cols.index("Название") + 1] == "Сектор"
    assert df.loc["AAAA", "Сектор"] == "Финансы" and df.loc["BBBB", "Сектор"] == "Энергетика"
    # T-Invest — как есть, ROE % -> доля
    assert df.loc["AAAA", "P/E"] == pytest.approx(4.2) and df.loc["AAAA", "ROE"] == pytest.approx(0.225)
    assert df.loc["AAAAP", "P/B"] == pytest.approx(0.9)
    # BBBB нет в T-Invest -> расчёт по CSV: кап. 100 млрд
    b = df.loc["BBBB"]
    assert (b["P/E"], b["P/B"], b["P/S"], b["EV/EBITDA"], b["ND/EBITDA"], b["ROE"]) == pytest.approx(
        (10.0, 2.0, 1.0, 5.5, 0.5, 0.2))
    assert "ZZZZ" not in df.index
    et = screener.etf_showcase().set_index("SECID")
    ecols = list(et.columns)
    assert ecols[ecols.index("Тип") + 1] == "Комиссия, %"
    assert et.loc["FNDA", "Комиссия, %"] == pytest.approx(0.95) and np.isnan(et.loc["FNDX", "Комиссия, %"])


def test_tinvest_multiples_missing_columns():
    out = screener.tinvest_multiples(pd.DataFrame({"pe": [5.0, None]}, index=["A", "B"]))
    assert list(out.columns) == screener.MULT_COLUMNS
    assert out.loc["A", "P/E"] == 5.0 and np.isnan(out.loc["B", "P/E"]) and out["ROE"].isna().all()
