"""Источник RusETFs (rusetfs.com, JSON-API скринера): нормализация, загрузка с повторами,
источник сборщика, чтение public/data (fund_info, приоритет комиссий), колонки витрины фондов
и смоук вкладки «Фонды» страницы «Витрина».

Без сети: HTTP-сессия подменяется фейком, паузы — через rusetfs._sleep, ответы — из
tests/fixtures/rusetfs/screener.json (данные выдуманные, структура — как у реального API
на 07.10.2026) или генерируются в тесте (~160 фондов для проверок здравости сборщика)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from core import config
from core.analytics import screener
from core.data import iss, public_data, rusetfs, tinvest
from scripts import collect_data as cd

FIX = Path(__file__).parent / "fixtures" / "rusetfs" / "screener.json"
ROOT = Path(__file__).resolve().parent.parent
TINVEST_SOURCES = ["tinvest_shares", "tinvest_etfs", "dividends", "fundamentals"]
_REAL_SESSION_FACTORY = cd._rusetfs_session    # до подмены autouse-фикстурой conftest
SITE_METRICS = ["sharpe", "sortino", "var5Percent", "revenueByCurrency", "fieldValuesByCurrency"]


def raw_fixture() -> list:
    return json.loads(FIX.read_text(encoding="utf-8"))


def _rec(**kw):
    """Запись фонда в формате normalize(): все поля None, кроме переданных."""
    return {**dict.fromkeys(rusetfs.FIELDS), **kw}


# ожидаемый результат normalize(screener.json) — посчитан вручную по фикстуре
EXPECTED = [
    _rec(ticker="TSTA", isin="RU000TEST0A1", name="Тест Акции РФ", issuer="УК Тест-Капитал",
         asset_class="Акции", asset_subclass="Широкий рынок", region="Россия", currency="RUB",
         commission_pct=0.79, aum_rub=123456789012.0, trading_start="2021-03-15",
         trade_status="Торгуется", active_management=False, dividend_policy="Реинвестирование"),
    _rec(ticker="TSTB", isin="RU000TEST0B1", name="Тест Облигации", issuer="УК Пример",
         asset_class="Облигации", asset_subclass="Корпоративные", region="Россия", currency="RUB",
         commission_pct=0.5, aum_rub=5e9, trading_start="2019-11-01", trade_status="Торгуется",
         active_management=True, dividend_policy="Выплата"),
    # " tstc " -> TSTC; комиссия 0 — это значение, а не «нет данных»; пустой словарь СЧА -> None
    _rec(ticker="TSTC", isin="RU000TEST0C1", name="Тест Малый регистр", issuer="УК Тест-Капитал",
         asset_class="Акции", asset_subclass="Дивидендные", region="Россия", currency="RUB",
         commission_pct=0.0, trading_start="2024-04-04", trade_status="Торгуется"),
    # дубль: торгуемый выпуск побеждает не торгуемый, хотя СЧА у него меньше
    _rec(ticker="TSTD", isin="RU000TEST0D1", name="Тест Дубль", issuer="УК Пример",
         asset_class="Денежный рынок", asset_subclass="Денежный рынок", region="Россия", currency="RUB",
         commission_pct=0.29, aum_rub=1e9, trading_start="2023-06-01", trade_status="Торгуется",
         active_management=False, dividend_policy="Реинвестирование"),
    # "NaN" / "-" / "—" / "" / "nan" везде -> None
    _rec(ticker="TSTE"),
    # нет tags и assetSubClass; комиссия строкой "0.012" -> 1.2 %
    _rec(ticker="TSTF", isin="RU000TEST0F1", name="Тест Золото", issuer="УК Тест-Капитал",
         asset_class="Товарный рынок", region="Россия", currency="RUB", commission_pct=1.2,
         aum_rub=250000000.0, trading_start="2020-02-03", trade_status="Торгуется"),
    # комиссия 25 % > 10 % -> None; СЧА < 0 -> None; tags = {}
    _rec(ticker="TSTG", isin="RU000TEST0G1", name="Тест Ошибка комиссии", issuer="УК Пример",
         asset_class="Смешанные", asset_subclass="Смешанные", region="Россия", currency="RUB",
         trading_start="2022-09-09", trade_status="Торгуется"),
]


def make_payload(n: int = 160, with_fee: int | None = None) -> list:
    """n фондов по шаблону TSTA (тикеры F000…); комиссия у первых with_fee (по умолчанию у всех)."""
    tmpl = raw_fixture()[0]
    with_fee = n if with_fee is None else with_fee
    out = []
    for i in range(n):
        r = copy.deepcopy(tmpl)
        r["ticker"] = f"F{i:03d}"
        r["isin"] = f"RU000F{i:06d}"
        r["commisionPercent"] = round(0.001 + i * 0.0001, 6) if i < with_fee else "NaN"
        r["assetsUnderManagementByCurrency"] = {"RUB": 1e8 * (i + 1)}
        out.append(r)
    return out


# ======================================================================= фейки
class FakeResp:
    def __init__(self, code: int, body=None, not_json: bool = False):
        self.status_code = code
        self._body = body
        self._not_json = not_json

    def json(self):
        if self._not_json:
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        return copy.deepcopy(self._body)


class FakeGetSession:
    """Подмена requests.Session для GET: script — ответы/исключения по порядку, затем default()."""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default
        self.calls: list[dict] = []

    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if self.script:
            item = self.script.pop(0)
        elif self.default is not None:
            item = self.default()
        else:
            raise AssertionError("лишний запрос")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch):
    rec: list[float] = []
    monkeypatch.setattr(rusetfs, "_sleep", rec.append)
    return rec


# ======================================================================= normalize
def test_normalize_fixture_exact():
    assert rusetfs.normalize(raw_fixture()) == EXPECTED


def test_normalize_keys_exact_and_ordered():
    out = rusetfs.normalize(raw_fixture())
    assert rusetfs.FIELDS == ["ticker", "isin", "name", "issuer", "asset_class", "asset_subclass", "region",
                              "currency", "commission_pct", "aum_rub", "trading_start", "trade_status",
                              "active_management", "dividend_policy"]
    for r in out:
        assert list(r) == rusetfs.FIELDS


def test_normalize_site_metrics_not_saved():
    out = rusetfs.normalize(raw_fixture())
    text = json.dumps(out, ensure_ascii=False)
    for k in SITE_METRICS + ["tags", "structure", "assetsUnderManagementByCurrency", "commisionPercent"]:
        assert k not in text
    assert "1500000000" not in text            # СЧА в USD не берётся, только RUB
    assert "1.23" not in text and "1.87" not in text


@pytest.mark.parametrize("raw, pct", [
    (0.005, 0.5), (0.0079, 0.79), ("0.012", 1.2), ("0,0095", 0.95), (0, 0.0), (0.1, 10.0),
    (0.10001, None), (-0.001, None), (0.25, None), ("NaN", None), ("-", None), ("", None),
    (None, None), (True, None), (float("nan"), None), (float("inf"), None), ("0.5%", None),
    ({"value": 0.005}, None),
])
def test_normalize_commission_units(raw, pct):
    (r,) = rusetfs.normalize([{"ticker": "X", "commisionPercent": raw}])
    assert r["commission_pct"] == pct if pct is not None else r["commission_pct"] is None
    if pct is not None:
        assert isinstance(r["commission_pct"], float)


def test_normalize_commission_rounding():
    # доля -> % с округлением до 4 знаков: нет хвостов float вроде 0.7900000000000001
    (r,) = rusetfs.normalize([{"ticker": "X", "commisionPercent": 0.0079}])
    assert repr(r["commission_pct"]) == "0.79"
    (r,) = rusetfs.normalize([{"ticker": "X", "commisionPercent": 0.001234567}])
    assert r["commission_pct"] == 0.1235


def test_normalize_correct_spelling_fallback():
    (r,) = rusetfs.normalize([{"ticker": "X", "commissionPercent": 0.004}])
    assert r["commission_pct"] == 0.4


def test_normalize_empty_and_garbage_input():
    assert rusetfs.normalize([]) == []
    assert rusetfs.normalize(None) == []
    assert rusetfs.normalize([None, 1, "x", [], {}, {"ticker": None}, {"ticker": "NaN"},
                              {"ticker": " - "}, {"ticker": ""}, {"ticker": True}]) == []


def test_normalize_single_fund_minimal():
    assert rusetfs.normalize([{"ticker": "ONE"}]) == [_rec(ticker="ONE")]


def test_normalize_sorted_by_ticker():
    raw = [{"ticker": t} for t in ["ZZZZ", "aaaa", "MMMM", "BBBB"]]
    assert [r["ticker"] for r in rusetfs.normalize(raw)] == ["AAAA", "BBBB", "MMMM", "ZZZZ"]


def test_normalize_duplicates_rules():
    def f(status, aum, isin):
        return {"ticker": "DUP", "isin": isin, "tradeStatus": {"name": status},
                "assetsUnderManagementByCurrency": {"RUB": aum}}
    # оба торгуются — больший СЧА
    assert rusetfs.normalize([f("Торгуется", 1, "A"), f("Торгуется", 5, "B")])[0]["isin"] == "B"
    assert rusetfs.normalize([f("Торгуется", 5, "A"), f("Торгуется", 1, "B")])[0]["isin"] == "A"
    # торгуемый побеждает, даже с меньшим СЧА и в любом порядке
    assert rusetfs.normalize([f("Торгуется", 1, "A"), f("Не торгуется", 9, "B")])[0]["isin"] == "A"
    assert rusetfs.normalize([f("Не торгуется", 9, "A"), f("Торгуется", 1, "B")])[0]["isin"] == "B"
    # равенство СЧА — первая; СЧА нет у одной — побеждает та, где есть
    assert rusetfs.normalize([f("Торгуется", 3, "A"), f("Торгуется", 3, "B")])[0]["isin"] == "A"
    assert rusetfs.normalize([f("Торгуется", "NaN", "A"), f("Торгуется", 0, "B")])[0]["isin"] == "B"
    # регистр и пробелы тикера не создают дубль
    out = rusetfs.normalize([{"ticker": "dup "}, {"ticker": "DUP"}, {"ticker": " Dup"}])
    assert len(out) == 1 and out[0]["ticker"] == "DUP"


def test_normalize_active_management_and_dates():
    def rec(model=None, start=None):
        r = {"ticker": "X", "tradingStartDate": start}
        if model is not None:
            r["tags"] = {"investmentPolicy": {"modelFollowing": model}}
        return rusetfs.normalize([r])[0]
    assert rec("Активная")["active_management"] is True
    assert rec("Пассивная")["active_management"] is False
    assert rec("Смешанная")["active_management"] is False
    assert rec("NaN")["active_management"] is None
    assert rec()["active_management"] is None
    assert rec(start="2021-03-15T10:00:00+03:00")["trading_start"] == "2021-03-15"
    assert rec(start="15.03.2021")["trading_start"] is None
    assert rec(start="NaN")["trading_start"] is None


def test_normalize_impossible_date_rejected():
    """Дата начала торгов должна быть настоящей датой: «2020-13-45» — мусор, а не дата."""
    r = rusetfs.normalize([{"ticker": "X", "tradingStartDate": "2020-13-45"}])[0]
    assert r["trading_start"] is None


def test_normalize_does_not_mutate_input():
    raw = raw_fixture()
    before = copy.deepcopy(raw)
    rusetfs.normalize(raw)
    assert raw == before


def test_normalize_generated_160():
    out = rusetfs.normalize(make_payload(160, with_fee=150))
    assert len(out) == 160
    assert sum(r["commission_pct"] is not None for r in out) == 150
    assert out[0]["commission_pct"] == 0.1 and out[1]["commission_pct"] == 0.11
    assert out[-1]["aum_rub"] == 1.6e10


# ======================================================================= fetch_screener
def test_fetch_ok_headers_url_timeout(sleeps):
    s = FakeGetSession([FakeResp(200, raw_fixture())])
    data = rusetfs.fetch_screener(session=s)
    assert data == raw_fixture()                          # как есть, без нормализации
    (call,) = s.calls
    assert call["url"] == config.RUSETFS_URL == "https://rusetfs.com/api/v1/screener"
    assert call["headers"]["User-Agent"] == rusetfs.USER_AGENT
    assert "InvestAnalytics" in call["headers"]["User-Agent"]
    assert "python-requests" not in call["headers"]["User-Agent"]
    assert call["headers"]["Accept"] == "application/json"
    assert call["timeout"] == 30
    assert call.get("verify", True) is not False           # проверка TLS не отключена
    assert sleeps == []


def test_fetch_empty_list_is_valid(sleeps):
    assert rusetfs.fetch_screener(session=FakeGetSession([FakeResp(200, [])])) == []


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_fetch_retry_on_429_and_5xx(code, sleeps):
    s = FakeGetSession([FakeResp(code, {"error": "x"}), FakeResp(200, [{"ticker": "A"}])])
    assert rusetfs.fetch_screener(session=s) == [{"ticker": "A"}]
    assert len(s.calls) == 2 and sleeps == [2.0]


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectionError("reset"),
                                 requests.exceptions.ReadTimeout("read timeout"),
                                 requests.exceptions.ConnectTimeout("connect timeout"),
                                 OSError("network unreachable")])
def test_fetch_retry_on_network_errors(exc, sleeps):
    s = FakeGetSession([exc, exc, FakeResp(200, [{"ticker": "A"}])])
    assert rusetfs.fetch_screener(session=s) == [{"ticker": "A"}]
    assert len(s.calls) == 3 and sleeps == [2.0, 4.0]


def test_fetch_retries_exhausted(sleeps):
    s = FakeGetSession(default=lambda: FakeResp(503))
    with pytest.raises(rusetfs.RusEtfsError) as ei:
        rusetfs.fetch_screener(session=s)
    assert len(s.calls) == 4                               # 1 + retries=3
    assert sleeps == [2.0, 4.0, 8.0]
    assert "4 попыток" in str(ei.value) and "HTTP 503" in str(ei.value)


def test_fetch_retries_exhausted_network(sleeps):
    s = FakeGetSession(default=lambda: requests.exceptions.ConnectionError("Name or service not known"))
    with pytest.raises(rusetfs.RusEtfsError, match="ConnectionError"):
        rusetfs.fetch_screener(session=s, retries=1)
    assert len(s.calls) == 2 and sleeps == [2.0]


def test_fetch_backoff_capped_at_60(sleeps):
    s = FakeGetSession(default=lambda: FakeResp(500))
    with pytest.raises(rusetfs.RusEtfsError):
        rusetfs.fetch_screener(session=s, retries=7, backoff=2.0)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_fetch_zero_retries(sleeps):
    s = FakeGetSession(default=lambda: FakeResp(502))
    with pytest.raises(rusetfs.RusEtfsError, match="1 попыток"):
        rusetfs.fetch_screener(session=s, retries=0)
    assert len(s.calls) == 1 and sleeps == []


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410])
def test_fetch_no_retry_on_4xx(code, sleeps):
    s = FakeGetSession(default=lambda: FakeResp(code))
    with pytest.raises(rusetfs.RusEtfsError, match=f"HTTP {code}"):
        rusetfs.fetch_screener(session=s)
    assert len(s.calls) == 1 and sleeps == []


def test_fetch_not_json_no_retry(sleeps):
    s = FakeGetSession(default=lambda: FakeResp(200, not_json=True))
    with pytest.raises(rusetfs.RusEtfsError, match="не JSON"):
        rusetfs.fetch_screener(session=s)
    assert len(s.calls) == 1 and sleeps == []


@pytest.mark.parametrize("body, kind", [({"data": []}, "dict"), ("text", "str"), (None, "NoneType"), (5, "int")])
def test_fetch_not_list_no_retry(body, kind, sleeps):
    s = FakeGetSession(default=lambda: FakeResp(200, body))
    with pytest.raises(rusetfs.RusEtfsError, match=kind):
        rusetfs.fetch_screener(session=s)
    assert len(s.calls) == 1 and sleeps == []


def test_fetch_ssl_error_no_retry(sleeps):
    s = FakeGetSession(default=lambda: requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"))
    with pytest.raises(rusetfs.RusEtfsError, match="TLS") as ei:
        rusetfs.fetch_screener(session=s)
    assert len(s.calls) == 1 and sleeps == []
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_fetch_error_is_runtime_error():
    assert issubclass(rusetfs.RusEtfsError, RuntimeError)


def test_fetch_default_session_created(monkeypatch, sleeps):
    made = []

    def factory():
        s = FakeGetSession([FakeResp(200, [])])
        made.append(s)
        return s
    monkeypatch.setattr(rusetfs.requests, "Session", factory)
    assert rusetfs.fetch_screener() == []
    assert len(made) == 1 and len(made[0].calls) == 1


def test_default_sleep_is_real_sleep():
    import time
    assert rusetfs._sleep is time.sleep


# ======================================================================= сборщик
@pytest.fixture
def rus(tmp_path, monkeypatch, sleeps):
    """Сборщик пишет в tmp_path (и приложение читает оттуда же); RusETFs — фейковая сессия."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", tmp_path)
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    state = {"payload": make_payload(), "sessions": [], "out": tmp_path, "sleeps": sleeps,
             "mode": None, "events": []}

    def respond():
        state["events"].append("rusetfs")
        if state["mode"] is not None:
            m = state["mode"]
            return m if isinstance(m, Exception) else FakeResp(m)
        return FakeResp(200, state["payload"])

    def factory():
        s = FakeGetSession(default=respond)
        state["sessions"].append(s)
        return s
    monkeypatch.setattr(cd, "_rusetfs_session", factory)

    def no_tinvest(*a, **k):
        raise AssertionError("без токена клиент T-Invest не создаётся")
    monkeypatch.setattr(tinvest, "TInvestClient", no_tinvest)
    return state


def _status(out: Path) -> dict:
    return json.loads((out / "status.json").read_text(encoding="utf-8"))


def _read(out: Path, name: str) -> dict:
    return json.loads((out / f"{name}.json").read_text(encoding="utf-8"))


def _cbr_good():
    return pd.Series(range(3500), index=pd.bdate_range("2010-01-01", periods=3500), dtype=float)


def test_rusetfs_source_registered():
    spec = cd.SOURCES["rusetfs_funds"]
    assert spec["kind"] == "rusetfs" and spec["min_rows"] == 150
    assert spec["source_url"] == config.RUSETFS_URL
    assert cd.RUSETFS_MIN_COMMISSION_SHARE == 0.80


def test_real_session_factory_returns_requests_session():
    # исходная фабрика (до подмены в conftest) — обычная requests.Session, запросов при создании нет
    s = _REAL_SESSION_FACTORY()
    assert isinstance(s, requests.Session)
    s.close()


def test_collector_rusetfs_file_format(rus):
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    out = rus["out"]
    f = _read(out, "rusetfs_funds")
    assert list(f) == ["updated", "source", "source_url", "data"]
    assert f["source"] == "RusETFs (rusetfs.com)" == cd.RUSETFS_SOURCE
    assert f["source_url"] == "https://rusetfs.com/api/v1/screener"
    assert f["data"] == rusetfs.normalize(rus["payload"])
    assert len(f["data"]) == 160 and all(list(r) == rusetfs.FIELDS for r in f["data"])
    assert f["data"][0]["commission_pct"] == 0.1                 # доля 0.001 -> 0.1 %
    text = (out / "rusetfs_funds.json").read_text(encoding="utf-8")
    for k in SITE_METRICS:
        assert k not in text
    assert "Тест Акции РФ" in text                                # ensure_ascii=False
    st = _status(out)["rusetfs_funds"]
    assert st["ok"] is True and st["rows"] == 160 and st["with_commission"] == 160
    assert "checked" in st and st["last"]
    # один запрос, одна сессия, UA проекта
    assert len(rus["sessions"]) == 1 and len(rus["sessions"][0].calls) == 1
    assert rus["sessions"][0].calls[0]["headers"]["User-Agent"] == rusetfs.USER_AGENT
    assert not [p for p in out.iterdir() if p.name.endswith(".tmp")]


def test_collector_updated_kept_when_data_unchanged(rus, monkeypatch):
    stamps = iter(["2026-10-01T03:00:00+00:00", "2026-10-02T03:00:00+00:00",
                   "2026-10-03T03:00:00+00:00", "2026-10-04T03:00:00+00:00",
                   "2026-10-05T03:00:00+00:00", "2026-10-06T03:00:00+00:00"])
    monkeypatch.setattr(cd, "_now", lambda: next(stamps))
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    first = _read(rus["out"], "rusetfs_funds")["updated"]
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    assert _read(rus["out"], "rusetfs_funds")["updated"] == first     # те же данные — дата прежняя
    # порядок записей в ответе не важен — normalize сортирует
    rus["payload"] = list(reversed(rus["payload"]))
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    assert _read(rus["out"], "rusetfs_funds")["updated"] == first
    # изменились данные — новая дата
    rus["payload"][0]["commisionPercent"] = 0.0333
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    assert _read(rus["out"], "rusetfs_funds")["updated"] != first


def _put_old(out: Path) -> str:
    old = json.dumps({"updated": "2026-01-01T00:00:00+00:00", "source": "RusETFs (rusetfs.com)",
                      "source_url": config.RUSETFS_URL, "data": [_rec(ticker="OLD", commission_pct=0.5)]},
                     ensure_ascii=False)
    (out / "rusetfs_funds.json").write_text(old, encoding="utf-8")
    (out / "status.json").write_text(json.dumps({"rusetfs_funds": {"ok": True, "rows": 1, "last": "2026-01-01"}}),
                                     encoding="utf-8")
    return old


@pytest.mark.parametrize("n, with_fee, ok", [
    (149, 149, False),     # фондов меньше 150
    (150, 150, True),      # ровно порог
    (150, 120, True),      # ровно 80 % с комиссией
    (150, 119, False),     # меньше 80 %
    (200, 159, False),     # 79,5 %
    (200, 160, True),
    (0, 0, False),         # пустой ответ
])
def test_collector_sanity_thresholds(rus, n, with_fee, ok):
    old = _put_old(rus["out"])
    rus["payload"] = make_payload(n, with_fee)
    assert cd.main(["--only", "rusetfs_funds"]) == (0 if ok else 1)
    st = _status(rus["out"])["rusetfs_funds"]
    assert st["ok"] is ok
    text = (rus["out"] / "rusetfs_funds.json").read_text(encoding="utf-8")
    if ok:
        assert st["rows"] == n and st["with_commission"] == with_fee
        assert len(json.loads(text)["data"]) == n
    else:
        assert text == old                                         # прежний файл цел
        assert st["last"] == "2026-01-01"                          # последние успешные данные
        assert "ValueError" in st["error"]
        assert ("слишком мало" in st["error"]) if n < 150 else ("80%" in st["error"])


def _payload_with_zeros(n: int, positive: int) -> list:
    """n фондов: у первых positive комиссия > 0, у остальных — ровно 0 (не «нет данных»)."""
    p = make_payload(n)
    for r in p[positive:]:
        r["commisionPercent"] = 0
    return p


def test_collector_sanity_all_zero_commissions_fail(rus):
    """160 фондов, у всех комиссия 0: ноль в санити не засчитывается -> сбой, прежний файл цел."""
    old = _put_old(rus["out"])
    rus["payload"] = _payload_with_zeros(160, 0)
    assert all(r["commission_pct"] == 0.0 for r in rusetfs.normalize(rus["payload"]))
    assert cd.main(["--only", "rusetfs_funds"]) == 1
    assert (rus["out"] / "rusetfs_funds.json").read_text(encoding="utf-8") == old
    st = _status(rus["out"])["rusetfs_funds"]
    assert st["ok"] is False and "ValueError" in st["error"] and "0 из 160" in st["error"]
    assert st["last"] == "2026-01-01"


@pytest.mark.parametrize("positive, ok", [(120, True), (119, False)])
def test_collector_sanity_zero_commissions_boundary(rus, positive, ok):
    """150 фондов: 120 с комиссией > 0 и 30 нулей — ровно 80 %, проходит; 119 + 31 ноль — сбой."""
    old = _put_old(rus["out"])
    rus["payload"] = _payload_with_zeros(150, positive)
    assert cd.main(["--only", "rusetfs_funds"]) == (0 if ok else 1)
    st = _status(rus["out"])["rusetfs_funds"]
    text = (rus["out"] / "rusetfs_funds.json").read_text(encoding="utf-8")
    if ok:
        assert st["ok"] is True and st["rows"] == 150 and st["with_commission"] == 120
        data = json.loads(text)["data"]
        assert sum(r["commission_pct"] == 0.0 for r in data) == 30   # нули сохраняются в файле как есть
    else:
        assert st["ok"] is False and text == old


def test_collector_rusetfs_failure_warning(rus, monkeypatch, capsys):
    """Сбой RusETFs при собранном ЦБ: код 0, но ::warning:: с краткой причиной."""
    sources = {"cbr_x": {**cd.SOURCES["cbr_gold"], "load": _cbr_good}, "rusetfs_funds": cd.SOURCES["rusetfs_funds"]}
    monkeypatch.setattr(cd, "SOURCES", sources)
    rus["mode"] = 404
    assert cd.main([]) == 0
    out = capsys.readouterr().out
    warn = [ln for ln in out.splitlines() if ln.startswith("::warning::RusETFs")]
    assert len(warn) == 1 and "HTTP 404" in warn[0] and len(warn[0]) < 300
    rus["mode"] = None
    assert cd.main([]) == 0
    assert "::warning::RusETFs" not in capsys.readouterr().out


def test_collector_sanity_counts_after_dedup(rus):
    """160 записей, но 20 из них — дубли тикеров: после нормализации 140 < 150 — сбой."""
    old = _put_old(rus["out"])
    p = make_payload(140)
    rus["payload"] = p + copy.deepcopy(p[:20])
    assert cd.main(["--only", "rusetfs_funds"]) == 1
    assert (rus["out"] / "rusetfs_funds.json").read_text(encoding="utf-8") == old


@pytest.mark.parametrize("mode, retried", [
    (503, True), (429, True), (requests.exceptions.ConnectionError("reset"), True),
    (404, False), (requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"), False),
])
def test_collector_source_unavailable_keeps_old_file(rus, mode, retried):
    old = _put_old(rus["out"])
    rus["mode"] = mode
    assert cd.main(["--only", "rusetfs_funds"]) == 1
    assert (rus["out"] / "rusetfs_funds.json").read_text(encoding="utf-8") == old
    st = _status(rus["out"])["rusetfs_funds"]
    assert st["ok"] is False and "RusEtfsError" in st["error"] and st["last"] == "2026-01-01"
    assert len(rus["sessions"][0].calls) == (4 if retried else 1)
    assert rus["sleeps"] == ([2.0, 4.0, 8.0] if retried else [])


def test_collector_order_cbr_rusetfs_tinvest(rus, monkeypatch):
    """--only в обратном порядке, всё равно: ЦБ -> RusETFs -> T-Invest; status.json после каждого."""
    out = rus["out"]
    snapshots = {}

    def cbr_load():
        rus["events"].append("cbr")
        return _cbr_good()

    def fake_ti(name, spec, ctx):
        rus["events"].append(name)
        snapshots[name] = _status(out)
        return {"ok": True, "rows": 1, "last": "2026-10-07"}
    sources = dict(cd.SOURCES)
    sources["cbr_x"] = {**cd.SOURCES["cbr_gold"], "load": cbr_load}
    monkeypatch.setattr(cd, "SOURCES", sources)
    monkeypatch.setattr(cd, "collect_tinvest", fake_ti)
    rc = cd.main(["--only", "fundamentals", "tinvest_etfs", "rusetfs_funds", "cbr_x"])
    assert rc == 0
    assert rus["events"] == ["cbr", "rusetfs", "fundamentals", "tinvest_etfs"]
    # к первому источнику T-Invest ЦБ и RusETFs уже записаны в status.json
    assert snapshots["fundamentals"]["cbr_x"]["ok"] and snapshots["fundamentals"]["rusetfs_funds"]["ok"]


def test_collector_default_order_includes_rusetfs_before_tinvest(monkeypatch, tmp_path):
    """Полный прогон без --only: RusETFs собирается после всех рядов ЦБ и до любого T-Invest."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    events = []
    sources = {}
    for k, v in cd.SOURCES.items():
        kind = v.get("kind")
        if kind is None:
            sources[k] = {**v, "load": (lambda k=k: events.append(k) or _cbr_good())}
        else:
            sources[k] = v
    monkeypatch.setattr(cd, "SOURCES", sources)
    monkeypatch.setattr(cd, "collect_tinvest", lambda name, spec, ctx: events.append(name) or
                        {"ok": True, "rows": 1, "last": "x"})
    monkeypatch.setattr(cd, "collect_rusetfs", lambda name, spec: events.append(name) or
                        {"ok": True, "rows": 1, "last": "x"})
    assert cd.main([]) == 0
    cbr = [k for k, v in cd.SOURCES.items() if v.get("kind") is None]
    ti = [k for k, v in cd.SOURCES.items() if v.get("kind") == "tinvest"]
    assert events == cbr + ["rusetfs_funds"] + ti


def test_collector_exit_code_only_rusetfs_failed(rus, monkeypatch):
    """ЦБ собран, RusETFs упал -> код 0 (сайт работает на прежнем файле), сбой виден в status.json."""
    sources = {"cbr_x": {**cd.SOURCES["cbr_gold"], "load": _cbr_good}, "rusetfs_funds": cd.SOURCES["rusetfs_funds"]}
    monkeypatch.setattr(cd, "SOURCES", sources)
    rus["mode"] = 503
    assert cd.main([]) == 0
    st = _status(rus["out"])
    assert st["cbr_x"]["ok"] is True and st["rusetfs_funds"]["ok"] is False
    # падает и ЦБ -> код 1
    sources["cbr_x"] = {**sources["cbr_x"], "load": lambda: (_ for _ in ()).throw(ConnectionError("ЦБ"))}
    assert cd.main([]) == 1


def test_collector_exit_code_rusetfs_failed_tinvest_skipped(rus):
    """Без токена: T-Invest пропущен, единственный реально собиравшийся источник (RusETFs) упал -> 1."""
    rus["mode"] = 500
    assert cd.main(["--only", "rusetfs_funds", *TINVEST_SOURCES]) == 1
    st = _status(rus["out"])
    assert all(st[n]["skipped"] for n in TINVEST_SOURCES)
    assert st["rusetfs_funds"]["ok"] is False and not st["rusetfs_funds"].get("skipped")


def test_collector_without_tinvest_token_rusetfs_collected(rus, capsys):
    assert cd.main(["--only", *TINVEST_SOURCES, "rusetfs_funds"]) == 0
    out = rus["out"]
    st = _status(out)
    assert st["rusetfs_funds"]["ok"] is True and st["rusetfs_funds"]["rows"] == 160
    assert all(st[n].get("skipped") for n in TINVEST_SOURCES)
    assert (out / "rusetfs_funds.json").exists()
    assert not any((out / f"{n}.json").exists() for n in TINVEST_SOURCES)
    assert "::warning::" not in capsys.readouterr().out


def test_collector_rusetfs_failure_does_not_stop_tinvest(rus, monkeypatch):
    rus["mode"] = requests.exceptions.SSLError("bad cert")
    called = []
    monkeypatch.setattr(cd, "collect_tinvest", lambda name, spec, ctx: called.append(name) or
                        {"ok": True, "rows": 1, "last": "x"})
    assert cd.main(["--only", "rusetfs_funds", "tinvest_etfs"]) == 0
    assert called == ["tinvest_etfs"]


def test_collector_output_readable_by_public_data(rus):
    """Файл сборщика читается fund_info() без преобразований (тот же каталог)."""
    rus["payload"] = make_payload(150) + [r for r in raw_fixture() if isinstance(r, dict)]
    assert cd.main(["--only", "rusetfs_funds"]) == 0
    fi = public_data.fund_info()
    assert len(fi) == 157 and fi.index.name == "ticker"
    assert fi.loc["TSTA", "commission_pct"] == 0.79 and fi.loc["TSTA", "issuer"] == "УК Тест-Капитал"
    assert fi.loc["TSTD", "aum_rub"] == 1e9
    assert np.isnan(fi.loc["TSTG", "commission_pct"])


# ======================================================================= public_data
@pytest.fixture
def pub(tmp_path, monkeypatch):
    d = tmp_path / "public"
    d.mkdir()
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", d)

    def put(name, data, source="T-Invest API"):
        (d / f"{name}.json").write_text(json.dumps({"updated": "2026-10-01T00:00:00+00:00",
                                                    "source": source, "data": data},
                                                   ensure_ascii=False), encoding="utf-8")
    put.dir = d
    return put


def put_rus(pub, rows):
    pub("rusetfs_funds", rows, source="RusETFs (rusetfs.com)")


def test_fund_info_missing_file(pub):
    fi = public_data.fund_info()
    assert fi.empty and list(fi.columns) == public_data.FUND_INFO_COLUMNS
    assert fi.index.name == "ticker"


@pytest.mark.parametrize("content", ["{не json", "[обрыв", "", '{"data": {"TSTA": 1}}', '{"data": null}',
                                     '{"data": []}', '{"data": [1, "x", null, {"ticker": null}]}', "[1, 2]"])
def test_fund_info_broken_or_empty(pub, content):
    (pub.dir / "rusetfs_funds.json").write_text(content, encoding="utf-8")
    fi = public_data.fund_info()
    assert fi.empty and list(fi.columns) == public_data.FUND_INFO_COLUMNS
    assert public_data.etf_commissions().empty


def test_fund_info_values(pub):
    put_rus(pub, rusetfs.normalize(raw_fixture()))
    fi = public_data.fund_info()
    assert list(fi.columns) == public_data.FUND_INFO_COLUMNS == [
        "name", "issuer", "asset_class", "asset_subclass", "commission_pct", "aum_rub",
        "trade_status", "trading_start", "active_management"]
    assert list(fi.index) == ["TSTA", "TSTB", "TSTC", "TSTD", "TSTE", "TSTF", "TSTG"]
    assert fi["commission_pct"].dtype == float and fi["aum_rub"].dtype == float
    assert fi.loc["TSTA"].to_dict() == {
        "name": "Тест Акции РФ", "issuer": "УК Тест-Капитал", "asset_class": "Акции",
        "asset_subclass": "Широкий рынок", "commission_pct": 0.79, "aum_rub": 123456789012.0,
        "trade_status": "Торгуется", "trading_start": "2021-03-15", "active_management": False}
    assert fi.loc["TSTB", "active_management"] is True
    assert fi.loc["TSTC", "active_management"] is None and fi.loc["TSTC", "commission_pct"] == 0.0
    assert np.isnan(fi.loc["TSTC", "aum_rub"])
    assert np.isnan(fi.loc["TSTE", "commission_pct"]) and pd.isna(fi.loc["TSTE", "issuer"])


def test_fund_info_single_fund_and_missing_columns(pub):
    put_rus(pub, [{"ticker": "ONE", "commission_pct": "0.5", "aum_rub": "abc"}])
    fi = public_data.fund_info()
    assert list(fi.index) == ["ONE"] and list(fi.columns) == public_data.FUND_INFO_COLUMNS
    assert fi.loc["ONE", "commission_pct"] == 0.5 and np.isnan(fi.loc["ONE", "aum_rub"])
    assert pd.isna(fi.loc["ONE", "issuer"]) and fi.loc["ONE", "active_management"] is None


def test_fund_info_duplicates_first_wins(pub):
    put_rus(pub, [{"ticker": "DUP", "issuer": "Первая", "commission_pct": 0.1},
                  {"ticker": "DUP", "issuer": "Вторая", "commission_pct": 0.2}])
    fi = public_data.fund_info()
    assert len(fi) == 1 and fi.loc["DUP", "issuer"] == "Первая"


def test_etf_commissions_priority(pub):
    put_rus(pub, [_rec(ticker="FNDA", commission_pct=0.5), _rec(ticker="FNDY", commission_pct=None),
                  _rec(ticker="FNDZ", commission_pct=1.2), _rec(ticker="FNDQ", commission_pct=0.0)])
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}, {"ticker": "FNDX", "fixed_commission": 0.7},
                         {"ticker": "FNDY", "fixed_commission": 0.3}, {"ticker": "FNDW", "fixed_commission": 0.0},
                         {"ticker": "FNDQ", "fixed_commission": 0.4}])
    fees = public_data.etf_commissions()
    assert fees.dtype == float and fees.name is None and fees.index.name is None
    assert fees["FNDA"] == 0.5          # RusETFs важнее T-Invest
    assert fees["FNDX"] == 0.7          # только в T-Invest
    assert fees["FNDY"] == 0.3          # в RusETFs комиссии нет -> T-Invest
    assert fees["FNDZ"] == 1.2          # только в RusETFs
    assert fees["FNDQ"] == 0.4          # 0 в RusETFs — скорее пропуск: ненулевое T-Invest важнее
    assert np.isnan(fees["FNDW"])       # T-Invest 0 -> NaN
    assert sorted(fees.index) == ["FNDA", "FNDQ", "FNDW", "FNDX", "FNDY", "FNDZ"]


def test_etf_commissions_zero_in_both_sources(pub):
    """Ноль RusETFs остаётся, только если у T-Invest значения нет (0 там -> NaN, тикера нет, файла нет)."""
    put_rus(pub, [_rec(ticker="ZERO", commission_pct=0.0), _rec(ticker="ZRUS", commission_pct=0.0),
                  _rec(ticker="POS", commission_pct=0.6)])
    fees = public_data.etf_commissions()                       # T-Invest файла нет
    assert fees.to_dict() == {"POS": 0.6, "ZERO": 0.0, "ZRUS": 0.0}
    pub("tinvest_etfs", [{"ticker": "ZERO", "fixed_commission": 0.0}, {"ticker": "POS", "fixed_commission": 0.0},
                         {"ticker": "OTHER", "fixed_commission": 0.9}])
    fees = public_data.etf_commissions()
    assert fees.dtype == float and fees.index.name is None and fees.name is None
    assert fees["ZERO"] == 0.0          # оба нуля -> 0.0, а не NaN
    assert fees["ZRUS"] == 0.0          # 0 в RusETFs, тикера нет в T-Invest -> 0.0
    assert fees["POS"] == 0.6           # ненулевое RusETFs против нуля T-Invest
    assert fees["OTHER"] == 0.9
    assert sorted(fees.index) == ["OTHER", "POS", "ZERO", "ZRUS"]


def test_etf_commissions_only_rusetfs(pub):
    put_rus(pub, [_rec(ticker="FNDA", commission_pct=0.5), _rec(ticker="FNDB")])
    fees = public_data.etf_commissions()
    assert fees.to_dict() == {"FNDA": 0.5}
    assert fees.dtype == float and fees.index.name is None and fees.name is None


def test_etf_commissions_only_tinvest(pub):
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}])
    assert public_data.etf_commissions().to_dict() == {"FNDA": 0.95}


def test_etf_commissions_rusetfs_without_any_fee_falls_back(pub):
    put_rus(pub, [_rec(ticker="FNDA"), _rec(ticker="FNDB")])
    assert public_data.etf_commissions().empty
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}])
    assert public_data.etf_commissions().to_dict() == {"FNDA": 0.95}


def test_tinvest_commissions_skip_non_dict(pub):
    pub("tinvest_etfs", [None, "x", {"ticker": "FNDA", "fixed_commission": 0.95}])
    assert public_data.etf_commissions().to_dict() == {"FNDA": 0.95}


# ======================================================================= витрина фондов
def _fake_market(monkeypatch, tmp_path):
    stocks = pd.DataFrame({
        "SECID": ["AAAA", "BBBB"], "SHORTNAME": ["Тест", "Второй"], "PRICE": [150.0, 50.0],
        "LASTTOPREVPRICE": [1.0, 0.0], "VALTODAY_RUR": [3e9, 1e9], "CAP": [500e9, 100e9], "LISTLEVEL": [1, 2]})
    etfs = pd.DataFrame({
        "SECID": ["FNDA", "FNDX", "FNDB"], "SHORTNAME": ["Фонд А", "Фонд Х", "Фонд Б"],
        "FUNDTYPE": ["БПИФ", "БПИФ", "БПИФ"], "PRICE": [10.0, 1.0, 5.0],
        "LASTTOPREVPRICE": [0.1, 0.2, 0.0], "VALTODAY_RUR": [1e8, 1e7, 5e6]})
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    px = pd.Series(np.linspace(100, 130, len(idx)), index=idx)

    def no_network(*a, **k):
        raise AssertionError("ISS в тесте недоступен")
    monkeypatch.setattr(iss, "get_json", no_network)
    monkeypatch.setattr(iss, "stocks", lambda board="TQBR": stocks.copy())
    monkeypatch.setattr(iss, "etfs", lambda: etfs.copy())
    monkeypatch.setattr(iss, "close_series", lambda secid, start="2000-01-01", end=None, **k: px)
    monkeypatch.setattr(iss, "dividends", lambda secid: pd.DataFrame())
    monkeypatch.setattr(screener, "FUNDAMENTALS_CSV", tmp_path / "no_fundamentals.csv")


RUS_ROWS = [
    _rec(ticker="FNDA", name="Фонд А", issuer="УК Тест-Капитал", asset_class="Акции",
         commission_pct=0.5, aum_rub=1_234_567_890.0, trade_status="Торгуется"),
    _rec(ticker="FNDB", name="Фонд Б", issuer="УК Пример", asset_class="Облигации",
         commission_pct=None, aum_rub=4_999_999.0, trade_status="Торгуется"),
    _rec(ticker="ZZZZ", issuer="УК Чужая", asset_class="Акции", commission_pct=0.1, aum_rub=1e9),
]


def test_showcase_rusetfs_columns(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    put_rus(pub, RUS_ROWS)
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}, {"ticker": "FNDX", "fixed_commission": 0.7}])
    et = screener.etf_showcase()
    cols = list(et.columns)
    i = cols.index("Тип")
    assert cols[i + 1:i + 5] == ["Комиссия, %", "УК", "СЧА, млрд руб.", "Класс активов"]
    assert list(et["SECID"]) == ["FNDA", "FNDX", "FNDB"]           # строки — из ISS, ZZZZ не добавлен
    et = et.set_index("SECID")
    a, x, b = et.loc["FNDA"], et.loc["FNDX"], et.loc["FNDB"]
    assert a["Комиссия, %"] == 0.5 and a["УК"] == "УК Тест-Капитал" and a["Класс активов"] == "Акции"
    assert a["СЧА, млрд руб."] == 1.23                              # 1 234 567 890 руб. -> 1,23 млрд
    assert b["СЧА, млрд руб."] == 0.0                               # 4,99 млн -> 0,00 млрд (округление)
    assert np.isnan(b["Комиссия, %"])                               # нет ни в RusETFs, ни в T-Invest
    assert x["Комиссия, %"] == 0.7                                  # из T-Invest
    assert pd.isna(x["УК"]) and np.isnan(x["СЧА, млрд руб."]) and pd.isna(x["Класс активов"])
    assert et["СЧА, млрд руб."].dtype == float
    for k in ("sharpe", "Sharpe", "aum_rub", "issuer"):
        assert k not in et.columns


def test_showcase_rusetfs_without_commissions(pub, tmp_path, monkeypatch):
    """В RusETFs нет ни одной комиссии и нет T-Invest: «Комиссия, %» нет, УК — сразу после «Тип»."""
    _fake_market(monkeypatch, tmp_path)
    put_rus(pub, [{**r, "commission_pct": None} for r in RUS_ROWS])
    et = screener.etf_showcase()
    cols = list(et.columns)
    assert "Комиссия, %" not in cols
    i = cols.index("Тип")
    assert cols[i + 1:i + 4] == ["УК", "СЧА, млрд руб.", "Класс активов"]


def test_showcase_without_any_fund_files(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    et = screener.etf_showcase()
    for c in ("Комиссия, %", "УК", "СЧА, млрд руб.", "Класс активов"):
        assert c not in et.columns
    assert list(et["SECID"]) == ["FNDA", "FNDX", "FNDB"]


def test_showcase_broken_rusetfs_file_tinvest_only(pub, tmp_path, monkeypatch):
    _fake_market(monkeypatch, tmp_path)
    (pub.dir / "rusetfs_funds.json").write_text("[обрыв", encoding="utf-8")
    pub("tinvest_etfs", [{"ticker": "FNDA", "fixed_commission": 0.95}])
    et = screener.etf_showcase()
    assert "УК" not in et.columns and "Комиссия, %" in et.columns
    assert et.set_index("SECID").loc["FNDA", "Комиссия, %"] == 0.95


# ======================================================================= UI: вкладка «Фонды»
st_testing = pytest.importorskip("streamlit.testing.v1")
PAGE = str(ROOT / "pages" / "1_Витрина.py")


def _run_page(monkeypatch, tmp_path, **state):
    import streamlit as st
    _fake_market(monkeypatch, tmp_path)
    monkeypatch.setattr(screener, "bonds_showcase", lambda board="TQOB": pd.DataFrame(
        {"SECID": ["SU1"], "Лет до погаш.": [3.0], "Оборот, руб.": [1e6], "Листинг": [1]}))
    monkeypatch.setattr(iss, "metals", lambda: pd.DataFrame(
        {"SECID": ["GLDRUB_TOM"], "NAME": ["Золото"], "PRICE": [9000.0], "LASTTOPREVPRICE": [0.1],
         "VALTODAY_RUR": [1e9], "UPDATETIME": ["10:00:00"]}))
    monkeypatch.setattr(iss, "indices", lambda: pd.DataFrame({"SECID": ["IMOEX"], "CURRENTVALUE": [3000.0]}))
    st.cache_data.clear()
    at = st_testing.AppTest.from_file(PAGE, default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def _etf_frame(at) -> pd.DataFrame:
    frames = [d.value for d in at.dataframe if "Тип" in d.value.columns]
    assert len(frames) == 1, [list(d.value.columns) for d in at.dataframe]
    return frames[0]


def _keys(widgets) -> set:
    return {w.key for w in widgets}


def test_ui_funds_tab_with_rusetfs(pub, tmp_path, monkeypatch):
    put_rus(pub, RUS_ROWS)
    pub("tinvest_etfs", [{"ticker": "FNDX", "fixed_commission": 0.7}])
    at = _run_page(monkeypatch, tmp_path)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    df = _etf_frame(at)
    for c in ("Комиссия, %", "УК", "СЧА, млрд руб.", "Класс активов"):
        assert c in df.columns
    assert len(df) == 3
    assert {"etf_uk", "etf_class"} <= _keys(at.multiselect)
    assert at.multiselect(key="etf_uk").options == ["УК Пример", "УК Тест-Капитал"]
    assert at.multiselect(key="etf_class").options == ["Акции", "Облигации"]
    assert "etf_fee_max" in _keys(at.number_input) and "etf_fee_sort" in _keys(at.toggle)
    captions = " ".join(c.value for c in at.caption)
    assert "rusetfs.com" in captions and "RusETFs" in captions

    # фильтр по УК
    at.multiselect(key="etf_uk").select("УК Тест-Капитал").run()
    assert not at.exception, at.exception
    assert list(_etf_frame(at)["SECID"]) == ["FNDA"]
    # плюс фильтр по классу, не совпадающий с УК — пусто, но без ошибок
    at.multiselect(key="etf_class").select("Облигации").run()
    assert not at.exception and _etf_frame(at).empty


def test_ui_funds_tab_fee_filter_and_sort(pub, tmp_path, monkeypatch):
    put_rus(pub, RUS_ROWS)
    pub("tinvest_etfs", [{"ticker": "FNDX", "fixed_commission": 0.7}])
    at = _run_page(monkeypatch, tmp_path)
    at.toggle(key="etf_fee_sort").set_value(True).run()
    assert not at.exception, at.exception
    assert list(_etf_frame(at)["SECID"]) == ["FNDA", "FNDX", "FNDB"]    # 0,5 < 0,7 < нет данных
    at.number_input(key="etf_fee_max").set_value(0.6).run()
    assert not at.exception, at.exception
    assert list(_etf_frame(at)["SECID"]) == ["FNDA"]
    assert any("скрыты" in c.value for c in at.caption)


def test_ui_funds_tab_rusetfs_only_class_and_uk_without_fee(pub, tmp_path, monkeypatch):
    put_rus(pub, [{**r, "commission_pct": None} for r in RUS_ROWS])
    at = _run_page(monkeypatch, tmp_path)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    df = _etf_frame(at)
    assert "Комиссия, %" not in df.columns and "УК" in df.columns
    assert {"etf_uk", "etf_class"} <= _keys(at.multiselect)
    assert "etf_fee_max" not in _keys(at.number_input)
    at.multiselect(key="etf_class").select("Облигации").run()
    assert not at.exception and list(_etf_frame(at)["SECID"]) == ["FNDB"]


def test_ui_funds_tab_without_fund_files(pub, tmp_path, monkeypatch):
    at = _run_page(monkeypatch, tmp_path)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    df = _etf_frame(at)
    for c in ("Комиссия, %", "УК", "СЧА, млрд руб.", "Класс активов"):
        assert c not in df.columns
    assert list(df["SECID"]) == ["FNDA", "FNDX", "FNDB"]
    keys = _keys(at.multiselect) | _keys(at.number_input) | _keys(at.toggle)
    assert not keys & {"etf_uk", "etf_class", "etf_fee_max", "etf_fee_sort"}


def test_ui_funds_tab_iss_error_shown(pub, tmp_path, monkeypatch):
    """ISS недоступен: вкладка показывает ошибку, страница не падает."""
    put_rus(pub, RUS_ROWS)

    def down():
        raise ConnectionError("ISS недоступен")
    import streamlit as st
    _fake_market(monkeypatch, tmp_path)
    monkeypatch.setattr(iss, "etfs", down)
    monkeypatch.setattr(screener, "bonds_showcase", lambda board="TQOB": pd.DataFrame(
        {"SECID": ["SU1"], "Лет до погаш.": [3.0], "Оборот, руб.": [1e6], "Листинг": [1]}))
    monkeypatch.setattr(iss, "metals", lambda: pd.DataFrame(
        {"SECID": ["X"], "NAME": ["X"], "PRICE": [1.0], "LASTTOPREVPRICE": [0.0],
         "VALTODAY_RUR": [1.0], "UPDATETIME": ["10:00:00"]}))
    monkeypatch.setattr(iss, "indices", lambda: pd.DataFrame({"SECID": ["IMOEX"]}))
    st.cache_data.clear()
    at = st_testing.AppTest.from_file(PAGE, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    assert [e.value for e in at.error] == ["Ошибка загрузки: ISS недоступен"]
