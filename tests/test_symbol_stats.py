"""Рейтинги риск/доходность: core/analytics/ranking.py, core/data/iss.monthly_closes и источник
symbol_stats сборщика scripts/collect_data.py (web/CONTRACT.md, «Ночной пересчёт»). Без сети:
загрузчик ISS подменяется через cd._symbol_stats_loader, часы бюджета — через cd._clock."""
from __future__ import annotations

import datetime as dt
import json
import math
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.analytics import metrics as m
from core.analytics import ranking
from core.data import iss
from scripts import collect_data as cd

ROOT = Path(__file__).resolve().parent.parent


# ================================================================ ranking.py
def test_percentile_ranks_ties_nan_inf():
    v = pd.Series({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0, "e": np.nan, "f": np.inf, "g": None}, dtype=object)
    r = ranking.percentile_ranks(v)
    # средние ранги 1, 2.5, 2.5, 4 из N = 4: (r − 1) / 3 · 100
    assert r[["a", "b", "c", "d"]].tolist() == pytest.approx([0, 50, 50, 100])
    assert r[["e", "f", "g"]].isna().all()


def test_percentile_ranks_single_and_empty():
    assert ranking.percentile_ranks(pd.Series({"x": 5.0, "y": np.nan})).tolist()[0] == 50
    assert ranking.percentile_ranks(pd.Series({"x": np.nan})).isna().all()
    assert ranking.percentile_ranks(pd.Series([], dtype=float)).empty
    # все равны — все в середине
    assert ranking.percentile_ranks(pd.Series([1.0, 1.0, 1.0])).tolist() == pytest.approx([50, 50, 50])


def test_rank_items_by_class_rounding_and_score():
    items = {
        "A": {"class": "share", "sharpe": 1.0, "sortino": 1.0, "omega": 2.0, "calmar": 0.5, "martin": 1.0},
        "B": {"class": "share", "sharpe": 2.0, "sortino": None, "omega": 1.0, "calmar": 0.1, "martin": 3.0},
        "C": {"class": "share", "sharpe": 0.5, "sortino": 3.0, "omega": 1.5, "calmar": 0.3, "martin": 2.0},
        "F": {"class": "fund", "sharpe": -5.0, "sortino": None, "omega": None, "calmar": None, "martin": None},
        "G": {"class": "fund", "sharpe": None, "sortino": None, "omega": None, "calmar": None, "martin": None},
    }
    out = ranking.rank_items(items)
    assert out["A"]["rank"] == {"sharpe": 50.0, "sortino": 0.0, "omega": 100.0, "calmar": 100.0, "martin": 0.0}
    assert out["B"]["rank"]["sortino"] is None                 # нет значения — нет ранга
    assert out["C"]["rank"]["sortino"] == 100.0                # среди двух значений
    assert out["A"]["score"] == pytest.approx(50.0)
    assert out["B"]["score"] == pytest.approx(round((100 + 0 + 0 + 100) / 4, 1))
    # фонды ранжируются отдельно: единственный — 50, независимо от акций
    assert out["F"]["rank"]["sharpe"] == 50.0 and out["F"]["score"] == 50.0
    assert out["G"]["score"] is None and all(v is None for v in out["G"]["rank"].values())


def test_rank_rounding_one_digit():
    items = {k: {"class": "share", "sharpe": float(i)} for i, k in enumerate("ABCD")}
    out = ranking.rank_items(items)
    assert [out[k]["rank"]["sharpe"] for k in "ABCD"] == [0.0, 33.3, 66.7, 100.0]


def test_symbol_metrics_matches_metrics_and_rounds():
    idx = pd.date_range("2020-01-31", periods=40, freq="ME")
    rng = np.random.default_rng(5)
    p = pd.Series(100 * np.cumprod(1 + rng.normal(0.01, 0.05, 40)), index=idx)
    out = ranking.symbol_metrics(p, 0.08)
    r = m.to_returns(p)
    assert out["months"] == 39
    assert out["sharpe"] == round(m.sharpe(r, 0.08, 12), 6)
    assert out["sortino"] == round(m.sortino(r, 0.08, 12), 6)
    assert out["omega"] == round(m.omega(r, 0.08, 12), 6)
    assert out["calmar"] == round(m.calmar(p), 6)
    assert out["martin"] == round(m.martin(p, 0.08), 6)
    assert out["cagr"] == round(m.cagr(p), 6)
    assert out["volatility"] == round(m.volatility(r, 12), 6)
    assert out["max_drawdown"] == round(m.max_drawdown(p), 6)
    assert list(out) == [*ranking.STAT_FIELDS, "months"]


def test_symbol_metrics_nan_becomes_none():
    p = pd.Series(np.full(40, 100.0), index=pd.date_range("2020-01-31", periods=40, freq="ME"))
    out = ranking.symbol_metrics(p, 0.1)
    assert out["volatility"] == 0
    assert out["sharpe"] is None and out["martin"] is None      # NaN -> None (JSON без NaN)
    json.dumps(out, allow_nan=False)


def test_mean_rate_calendar_days():
    rate = pd.Series([0.16, 0.20], index=pd.to_datetime(["2024-01-01", "2024-01-11"]))
    assert ranking.mean_rate(rate, "2024-01-01", "2024-01-20") == pytest.approx(0.18)    # 10 дн. × 16 % + 10 × 20 %
    assert ranking.mean_rate(rate, "2023-12-27", "2024-01-05") == pytest.approx(0.16)    # до первой даты — первое значение
    assert ranking.mean_rate(rate, "2024-02-01", "2024-02-29") == pytest.approx(0.20)    # после последней — ffill
    assert math.isnan(ranking.mean_rate(pd.Series([], dtype=float), "2024-01-01", "2024-01-02"))
    assert math.isnan(ranking.mean_rate(rate, "2024-02-01", "2024-01-01"))


# ================================================================ iss.monthly_closes
def test_iss_monthly_closes(monkeypatch):
    from core.data import iss
    calls = []

    def fake_candles(secid, start, end, interval, engine, market, board):
        calls.append((secid, start, end, interval, engine, market, board))
        idx = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-02-01", "2024-03-01", "2024-04-01"])
        return pd.DataFrame({"close": [10.0, 11.0, 12.0, 0.0, 13.0]}, index=idx)
    monkeypatch.setattr(iss, "candles", fake_candles)
    s = iss.monthly_closes("SBER", "2024-01-01", "2024-04-30")
    assert calls == [("SBER", "2024-01-01", "2024-04-30", "M", "stock", "shares", "TQBR")]
    assert list(s.index.strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-29", "2024-04-30"]   # конец месяца, 0 отброшен
    assert s.tolist() == [10.0, 12.0, 13.0] and s.name == "SBER"                             # дубль — последний
    monkeypatch.setattr(iss, "candles", lambda *a, **k: pd.DataFrame())
    assert iss.monthly_closes("X", "2024-01-01").empty


# ================================================================ сборщик: symbol_stats
def _till():
    return dt.date.today().replace(day=1) - dt.timedelta(days=1)


class FakeLoader:
    """securities() — n_shares акций (SECTYPE 1/2/D по кругу), n_funds фондов (J/9/A/B), облигация и мусор.
    monthly_closes — детерминированный ряд на окне [start, end] (месячные концы) плюс точки вне окна."""

    def __init__(self, n_shares=110, n_funds=12, short=(), months=None, fail=(), fail_all_after=None):
        self.n_shares, self.n_funds = n_shares, n_funds
        self.short = dict(short)            # secid -> число точек
        self.months = months or {}
        self.fail = set(fail)
        self.fail_all_after = fail_all_after
        self.calls: list[tuple] = []

    def securities(self):
        rows = [(f"S{i:03d}", "12D"[i % 3]) for i in range(self.n_shares)]
        rows += [(f"F{i:02d}", "J9AB"[i % 4]) for i in range(self.n_funds)]
        rows += [("BOND1", "6"), ("FUT", None)]
        return pd.DataFrame(rows, columns=["SECID", "SECTYPE"])

    def series(self, secid, start, end):
        idx = pd.date_range(start, end, freq="ME")
        seed = zlib.crc32(secid.encode())
        rng = np.random.default_rng(seed)
        s = pd.Series(100 * np.cumprod(1 + rng.normal(0.01, 0.06, len(idx))), index=idx)
        if secid in self.short:
            s = s.iloc[-self.short[secid]:]
        return s

    def monthly_closes(self, secid, start, end):
        self.calls.append((secid, start, end))
        if secid in self.fail or (self.fail_all_after is not None and len(self.calls) > self.fail_all_after):
            raise ConnectionError(f"ISS 503 для {secid}")
        s = self.series(secid, start, end)
        # точки вне окна (до базы и в текущем месяце) — сборщик их отбрасывает
        extra = pd.Series([1.0, 1.0], index=[pd.Timestamp(start) - pd.Timedelta(days=40),
                                             pd.Timestamp(end) + pd.Timedelta(days=15)])
        return pd.concat([extra.iloc[:1], s, extra.iloc[1:]])


@pytest.fixture
def env(tmp_path, monkeypatch):
    """OUT -> tmp, key_rate.json с постоянной ставкой 10 %, только источник symbol_stats."""
    monkeypatch.setattr(cd, "OUT", tmp_path)
    days = pd.bdate_range("2013-09-17", dt.date.today())
    (tmp_path / "key_rate.json").write_text(json.dumps(
        {"id": "key_rate", "data": [[d.strftime("%Y-%m-%d"), 0.1] for d in days]}), encoding="utf-8")
    loader = FakeLoader()
    monkeypatch.setattr(cd, "_symbol_stats_loader", lambda: loader)
    return tmp_path, loader


def _read(p: Path, name: str) -> dict:
    return json.loads((p / f"{name}.json").read_text(encoding="utf-8"))


def test_symbol_stats_happy_path(env):
    out, loader = env
    loader.short = {"S005": 37, "S006": 36, "F03": 10}       # 36 доходностей — входит, 35 — нет
    assert cd.main(["--only", "symbol_stats"]) == 0
    d = _read(out, "symbol_stats")
    till = _till()
    first = (pd.Timestamp(till) - pd.DateOffset(months=120)).to_period("M")
    assert d["source"] == "ISS MOEX (расчёт ИнвестАналитики)"
    assert d["rf"] == pytest.approx(0.1)
    assert d["window"] == {"from": first.to_timestamp("M").date().isoformat(), "till": till.isoformat(),
                           "max_months": 120, "min_months": 36}
    # загрузчик: окно от 1-го числа базового месяца до конца прошлого месяца; только акции и фонды
    secs = {c[0] for c in loader.calls}
    assert "BOND1" not in secs and "FUT" not in secs and len(secs) == 122
    assert loader.calls[0][1:] == (first.to_timestamp().date().isoformat(), till.isoformat())
    items = d["items"]
    assert "S006" not in items and "F03" not in items and items["S005"]["months"] == 36
    assert d["classes"] == {"share": {"n": 109}, "fund": {"n": 11}}
    assert list(items) == sorted(items)
    sber = items["S001"]
    assert sber["class"] == "share" and sber["months"] == 120
    # значения — те же формулы на ряде без точек вне окна, rf = 10 %
    s = loader.series("S001", *loader.calls[0][1:])
    exp = ranking.symbol_metrics(s, 0.1)
    for k in ranking.STAT_FIELDS:
        assert sber[k] == exp[k], k
    assert set(sber["rank"]) == set(ranking.RANK_METRICS)
    assert 0 <= sber["score"] <= 100
    assert items["F01"]["class"] == "fund"
    st = _read(out, "status")["symbol_stats"]
    assert st["ok"] and st["rows"] == 120 and st["shares"] == 109 and st["funds"] == 11
    assert st["short_history"] == 2 and st["failed"] == 0 and st["last"] == till.isoformat()


def test_symbol_stats_file_has_no_quotes(env):
    out, loader = env
    assert cd.main(["--only", "symbol_stats"]) == 0
    d = _read(out, "symbol_stats")
    assert set(d) == {"updated", "source", "total_return", "split_adjusted", "rf", "window", "classes", "items"}
    assert d["total_return"] is False                     # в tmp нет dividends.json — акции по ценам
    assert d["split_adjusted"] is True                    # свечи ISS скорректированы биржей
    assert not any(it["tr"] for it in d["items"].values())
    allowed = {"class", "tr", *ranking.STAT_FIELDS, "months", "rank", "score"}
    for sec, it in d["items"].items():
        assert set(it) == allowed, sec
        assert not any(isinstance(v, list) for v in it.values()), sec
        # ни одна цена закрытия не попала в файл
    prices = set(np.round(loader.series("S001", *loader.calls[0][1:]).to_numpy(), 6))
    assert not prices & {v for v in d["items"]["S001"].values() if isinstance(v, float)}
    raw = (out / "symbol_stats.json").read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw


def test_symbol_stats_ranks_within_class_ranges(env):
    out, _ = env
    assert cd.main(["--only", "symbol_stats"]) == 0
    items = _read(out, "symbol_stats")["items"]
    for cls in ("share", "fund"):
        sh = sorted((v["sharpe"], v["rank"]["sharpe"]) for v in items.values() if v["class"] == cls)
        assert sh[0][1] == 0.0 and sh[-1][1] == 100.0
        assert [r for _, r in sh] == sorted(r for _, r in sh)   # ранг монотонен по коэффициенту


def test_symbol_stats_updated_kept_when_unchanged(env, monkeypatch):
    out, loader = env
    monkeypatch.setattr(cd, "_now", lambda: "2026-10-08T03:00:00+00:00")
    assert cd.main(["--only", "symbol_stats"]) == 0
    monkeypatch.setattr(cd, "_now", lambda: "2026-10-09T03:00:00+00:00")
    assert cd.main(["--only", "symbol_stats"]) == 0
    assert _read(out, "symbol_stats")["updated"] == "2026-10-08T03:00:00+00:00"
    assert _read(out, "status")["symbol_stats"]["checked"] == "2026-10-09T03:00:00+00:00"
    loader.short = {"S001": 40}                     # данные изменились — updated новый
    assert cd.main(["--only", "symbol_stats"]) == 0
    assert _read(out, "symbol_stats")["updated"] == "2026-10-09T03:00:00+00:00"


def _seed_previous(out: Path) -> str:
    text = json.dumps({"updated": "2026-01-01", "source": "x", "items": {"OLD": {"class": "share"}}})
    (out / "symbol_stats.json").write_text(text, encoding="utf-8")
    return text


def test_symbol_stats_sanity_less_than_100_shares(env):
    out, loader = env
    before = _seed_previous(out)
    loader.n_shares = 99
    assert cd.main(["--only", "symbol_stats"]) == 1
    assert (out / "symbol_stats.json").read_text(encoding="utf-8") == before
    st = _read(out, "status")["symbol_stats"]
    assert not st["ok"] and "36" in st["error"]
    assert not list(out.glob(".*.tmp"))


def test_symbol_stats_exactly_100_shares_ok(env):
    out, loader = env
    loader.n_shares = 100
    assert cd.main(["--only", "symbol_stats"]) == 0
    assert _read(out, "symbol_stats")["classes"]["share"]["n"] == 100


def test_symbol_stats_budget(env, monkeypatch):
    out, loader = env
    before = _seed_previous(out)
    t = iter(range(0, 10_000_000, 7))                # каждый вызов часов +7 с: 600 с кончатся примерно на 85-й бумаге
    monkeypatch.setattr(cd, "_clock", lambda: next(t))
    assert cd.main(["--only", "symbol_stats"]) == 1
    st = _read(out, "status")["symbol_stats"]
    assert "бюджет" in st["error"]
    assert (out / "symbol_stats.json").read_text(encoding="utf-8") == before
    assert 40 < len(loader.calls) < 100 and len(loader.calls) < 122


def test_symbol_stats_consecutive_failures_abort(env):
    out, loader = env
    before = _seed_previous(out)
    loader.fail_all_after = 5                        # ISS «упал» после 5 бумаг
    assert cd.main(["--only", "symbol_stats"]) == 1
    assert len(loader.calls) == 5 + cd.MAX_CONSECUTIVE_FAILS     # прерван сразу после 10 сбоев подряд
    assert "подряд" in _read(out, "status")["symbol_stats"]["error"]
    assert (out / "symbol_stats.json").read_text(encoding="utf-8") == before


def test_symbol_stats_streak_resets_and_tolerates_few_failures(env):
    out, loader = env
    secs = sorted(loader.securities()["SECID"].dropna())
    secs = [s for s in secs if s not in ("BOND1", "FUT")]
    loader.fail = set(secs[:9] + secs[10:19])        # 9 подряд, успех, ещё 9 — серия сбрасывается; 18/122 < 20 %
    assert cd.main(["--only", "symbol_stats"]) == 0
    st = _read(out, "status")["symbol_stats"]
    assert st["ok"] and st["failed"] == 18
    assert not set(_read(out, "symbol_stats")["items"]) & loader.fail


def test_symbol_stats_failure_share_over_20pct(env):
    out, loader = env
    before = _seed_previous(out)
    secs = sorted(s for s in loader.securities()["SECID"].dropna() if s not in ("BOND1", "FUT"))
    loader.fail = set(secs[::4])                     # 25 %, но не подряд
    assert cd.main(["--only", "symbol_stats"]) == 1
    assert "20%" in _read(out, "status")["symbol_stats"]["error"]
    assert (out / "symbol_stats.json").read_text(encoding="utf-8") == before


def test_symbol_stats_without_key_rate(env):
    out, loader = env
    (out / "key_rate.json").unlink()
    assert cd.main(["--only", "symbol_stats"]) == 1
    assert "key_rate" in _read(out, "status")["symbol_stats"]["error"]
    assert loader.calls == []
    assert not (out / "symbol_stats.json").exists()


def test_symbol_stats_write_failure_keeps_previous(env, monkeypatch):
    out, _ = env
    before = _seed_previous(out)
    real = Path.replace

    def boom(self, target):
        if str(target).endswith("symbol_stats.json"):
            raise OSError("диск переполнен")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", boom)
    assert cd.main(["--only", "symbol_stats"]) == 1
    assert (out / "symbol_stats.json").read_text(encoding="utf-8") == before
    assert not list(out.glob(".symbol_stats.json.tmp"))


# ================================================================ main(): --skip и default
def _cbr_like(load):
    return {**cd.SOURCES["cbr_gold"], "load": load, "min_rows": 1}


def test_main_without_args_does_not_run_symbol_stats(env, monkeypatch):
    out, loader = env
    good = pd.Series([1.0, 2.0], index=pd.bdate_range("2025-01-01", periods=2))
    monkeypatch.setattr(cd, "SOURCES", {"a": _cbr_like(lambda: good), "symbol_stats": cd.SOURCES["symbol_stats"]})
    assert cd.main([]) == 0
    assert loader.calls == []
    assert "symbol_stats" not in _read(out, "status")
    assert not (out / "symbol_stats.json").exists()


def test_skip_flag(env, monkeypatch):
    out, loader = env
    ran = []
    good = pd.Series([1.0, 2.0], index=pd.bdate_range("2025-01-01", periods=2))
    monkeypatch.setattr(cd, "SOURCES", {"a": _cbr_like(lambda: ran.append("a") or good),
                                        "b": _cbr_like(lambda: ran.append("b") or good),
                                        "symbol_stats": cd.SOURCES["symbol_stats"]})
    assert cd.main(["--skip", "a"]) == 0
    assert ran == ["b"] and loader.calls == []
    ran.clear()
    # --skip применяется после --only
    assert cd.main(["--only", "symbol_stats", "a", "--skip", "symbol_stats"]) == 0
    assert ran == ["a"] and loader.calls == []
    # всё пропущено — ничего не пытались собрать, это не сбой
    assert cd.main(["--only", "symbol_stats", "--skip", "symbol_stats"]) == 0
    assert loader.calls == []
    with pytest.raises(SystemExit):
        cd.main(["--skip", "nope"])


def test_symbol_stats_runs_after_other_sources(env, monkeypatch):
    out, loader = env
    order = []
    good = pd.Series([1.0, 2.0], index=pd.bdate_range("2025-01-01", periods=2))
    real = cd.collect_symbol_stats
    monkeypatch.setattr(cd, "collect_symbol_stats", lambda n, s: order.append(n) or real(n, s))
    monkeypatch.setattr(cd, "SOURCES", {"symbol_stats": cd.SOURCES["symbol_stats"],
                                        "a": _cbr_like(lambda: order.append("a") or good)})
    assert cd.main(["--only", "symbol_stats", "a"]) == 0
    assert order == ["a", "symbol_stats"]


def test_symbol_stats_source_spec():
    spec = cd.SOURCES["symbol_stats"]
    assert spec["kind"] == "iss" and spec["default"] is False
    assert spec["min_rows"] == 100 and spec["budget_min"] == 10


# ================================================================ workflow
def test_workflow_steps():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "collect-data.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["collect"]
    steps = {s.get("name"): s for s in job["steps"]}
    collect = steps["Сбор"]
    assert "--skip symbol_stats" in collect["run"]
    assert "TINVEST_TOKEN" in collect.get("env", {})
    rating = steps["Рейтинги"]
    assert rating["timeout-minutes"] == 15
    assert "--only symbol_stats" in rating["run"]
    assert "TINVEST_TOKEN" not in json.dumps(rating)          # рейтингам токен не нужен
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Сбор") < names.index("Рейтинги") < names.index("Сохранить изменения")
    assert steps["Сохранить изменения"]["if"] == "always()"
    sbor = collect.get("timeout-minutes")
    assert job["timeout-minutes"] >= (sbor or 25) + 15


# ================================================================ полная доходность акций (второй круг)
def test_ex_dividends_filters_and_ex_date():
    rows = [
        {"record_date": "2025-07-21", "last_buy_date": "2025-07-18", "value": 34.84, "currency": "RUB"},   # пт -> пн
        {"record_date": "2024-07-11", "last_buy_date": "2024-07-10", "value": 33.3, "currency": "rub"},
        {"record_date": "2023-05-11", "value": 25},                                   # без last_buy_date — record_date
        {"record_date": "2022-05-12", "last_buy_date": "2022-05-10", "value": 18.7, "cancelled": True},
        {"record_date": "2021-05-12", "last_buy_date": "2021-05-10", "value": 2.0, "currency": "USD"},
        {"record_date": "2020-05-12", "value": None}, {"value": 1.0}, None, "мусор",
    ]
    out = ranking.ex_dividends(rows)
    assert sorted(out, key=lambda x: x["exDate"]) == [
        {"exDate": "2023-05-11", "value": 25.0}, {"exDate": "2024-07-11", "value": 33.3},
        {"exDate": "2025-07-21", "value": 34.84}]
    assert ranking.ex_dividends(None) == [] and ranking.ex_dividends([]) == []


def _div_rows(window_start: str, window_end: str) -> dict:
    mid = (pd.Timestamp(window_start) + (pd.Timestamp(window_end) - pd.Timestamp(window_start)) / 2).date().isoformat()
    late = (pd.Timestamp(window_end) - pd.DateOffset(months=6)).date().isoformat()
    return {
        "S001": [{"record_date": mid, "last_buy_date": mid, "value": 10.0, "currency": "RUB"},
                 {"record_date": late, "value": 5.0}],
        # только отменённые и долларовые — как будто дивидендов нет
        "S002": [{"record_date": mid, "value": 50.0, "cancelled": True},
                 {"record_date": late, "value": 50.0, "currency": "USD"}],
        "F01": [{"record_date": mid, "value": 10.0}],                       # у фондов дивиденды не учитываются
    }


def test_symbol_stats_shares_by_total_return(env):
    out, loader = env
    assert cd.main(["--only", "symbol_stats"]) == 0              # первый прогон — узнать окно запроса
    start, end = loader.calls[0][1:]
    divs = _div_rows(start, end)
    (out / "dividends.json").write_text(json.dumps({"updated": "x", "source": "T-Invest API", "data": divs}),
                                        encoding="utf-8")
    assert cd.main(["--only", "symbol_stats"]) == 0
    d = _read(out, "symbol_stats")
    assert d["total_return"] is True
    items = d["items"]
    assert all(it["tr"] is (it["class"] == "share") for it in items.values())
    price = {k: ranking.symbol_metrics(loader.series(k, start, end), 0.1) for k in ("S001", "S002", "F01", "S003")}
    tr = ranking.symbol_metrics(m.total_return_series(loader.series("S001", start, end),
                                                      ranking.ex_dividends(divs["S001"])), 0.1)
    for k in ranking.STAT_FIELDS:
        assert items["S001"][k] == tr[k], k
        assert items["S002"][k] == price["S002"][k], f"S002.{k}: отменённые и USD не учитываются"
        assert items["F01"][k] == price["F01"][k], f"F01.{k}: фонды — по ценам"
        assert items["S003"][k] == price["S003"][k], f"S003.{k}: нет выплат — как по ценам"
    assert items["S001"]["cagr"] > price["S001"]["cagr"], "дивиденды повышают CAGR"
    assert _read(out, "status")["symbol_stats"]["total_return"] is True


def test_symbol_stats_empty_dividends_file_is_price_only(env):
    out, _ = env
    (out / "dividends.json").write_text(json.dumps({"data": {}}), encoding="utf-8")
    assert cd.main(["--only", "symbol_stats"]) == 0
    d = _read(out, "symbol_stats")
    assert d["total_return"] is False and not any(it["tr"] for it in d["items"].values())


def test_workflow_ratings_step_condition():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "collect-data.yml").read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in wf["jobs"]["collect"]["steps"]}
    cond = steps["Рейтинги"].get("if", "").replace(" ", "")
    # выполняется и после сбоя «Сбора» (код 1), но не при отмене
    assert cond in ("success()||failure()", "${{success()||failure()}}"), cond
    assert "always()" not in cond and "cancelled" not in cond
    assert steps["Сохранить изменения"]["if"] == "always()"


def test_symbol_stats_security_without_candles_is_skipped(env, monkeypatch):
    """Регрессия (живой прогон 08.10.2026): у бумаги нет месячных свечей за окно — пустой ряд не роняет
    источник (раньше: TypeError при сравнении RangeIndex с Timestamp), бумага считается «короткой»."""
    out, loader = env
    orig = loader.monthly_closes

    def mc(sec, start, end):
        if sec == "S001":
            return pd.Series(dtype=float)            # как отдавал iss.monthly_closes без свечей
        return orig(sec, start, end)

    monkeypatch.setattr(loader, "monthly_closes", mc)
    assert cd.main(["--only", "symbol_stats"]) == 0
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))["symbol_stats"]
    assert st["ok"] and st["short_history"] >= 1
    assert "S001" not in _read(out, "symbol_stats")["items"]


def test_monthly_closes_empty_has_datetime_index(monkeypatch):
    monkeypatch.setattr(iss, "candles", lambda *a, **k: pd.DataFrame())
    s = iss.monthly_closes("NEWX", "2016-01-01", "2026-09-30")
    assert s.empty and isinstance(s.index, pd.DatetimeIndex) and s.attrs["split_adjusted"] is True
