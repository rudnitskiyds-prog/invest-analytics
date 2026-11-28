"""Логотипы УК: core/analytics/uk_logos.py, шаг сборщика uk_logos, public/data/uk_logos.json (без сети).
Контракт — web/CONTRACT.md, раздел C."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core import config
from core.analytics import uk_logos as ul
from scripts import collect_data as cd

DATA = config.ROOT / "public" / "data"


def _etf(t, logo=None, color="#111111"):
    return {"ticker": t, "name": t, "logo": logo, "logo_color": color if logo else None}


def _fund(t, uk):
    return {"ticker": t, "issuer": uk}


# ---------------------------------------------------------------- slug / issuer_key
@pytest.mark.parametrize("name, want", [
    ("Альфа Капитал", "alfa-kapital"), ("Ренессанс Капитал", "renessans-kapital"), ("ААА", "aaa"),
    ("ДоходЪ", "dokhod"), ("Райффайзен", "rayffayzen"), ("М-Консалтинг", "m-konsalting"),
    ("ITI FUNDS", "iti-funds"), ("Т-Капитал", "t-kapital"), ("АК БАРС", "ak-bars"),
])
def test_slug(name, want):
    assert ul.slug(name) == want
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", ul.slug(name))


def test_slug_of_every_public_uk_is_valid_and_unique():
    uk = json.loads((DATA / "uk_logos.json").read_text(encoding="utf-8"))["uk"]
    slugs = [ul.slug(n) for n in uk]
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s) for s in slugs)
    assert len(set(slugs)) == len(slugs), "два названия УК дали один slug — файлы логотипов перепутаются"


@pytest.mark.parametrize("name, want", [
    ("ВТБ Б1-243", "ВТБ Б"), ("Озон 1P-02", "Озон"), ("ОФЗ 26245", "ОФЗ"), ("Россельхозбанк", "Россельхозбанк"),
    ("Республика Беларусь 331 29.06.2027", "Республика Беларусь"), ("", ""), (None, ""), ("1P-02", ""),
])
def test_issuer_key(name, want):
    assert ul.issuer_key(name) == want


# ---------------------------------------------------------------- build: логотип УК
def _build(monkeypatch, sources, funds, etfs=(), shares=(), bonds=(), screener=None, web_dir=None):
    monkeypatch.setattr(ul, "UK_LOGO_SOURCES", sources)
    return ul.build(list(funds), list(etfs), list(shares), list(bonds), screener, web_dir=web_dir)


def test_build_explicit_sources_and_group_flag(monkeypatch):
    sources = {
        "УК-акция": {"share": "SH1"},
        "УК-фонд": {"etf": "E3"},
        "УК-файл": {"logo": "x.png", "color": "#112233"},
        "УК-файл-цвет": {"logo": "colored.png"},          # цвет — из справочника по имени файла
        "УК-эмитент": {"bond_issuer": "Эмитент", "group": True},
        "УК-нет": {"share": "NOPE"},                      # бумаги нет в справочнике -> без логотипа
    }
    funds = [_fund(f"F{i}", n) for i, n in enumerate(sources)]
    shares = [{"ticker": "SH1", "name": "Акция", "logo": "sh1.png", "logo_color": "#AA0000"}]
    etfs = [_etf("E3", "e3.png", "#00BB00"), _etf("F3", "colored.png", "#ABCDEF")]
    bonds = [{"ticker": "B1", "name": "Эмитент 1P-01", "logo": "iss.png", "logo_color": "#0000CC"},
             {"ticker": "B2", "name": "Эмитент 1P-02", "logo": "iss.png", "logo_color": "#0000CC"}]
    uk = _build(monkeypatch, sources, funds, etfs, shares, bonds)["uk"]
    assert uk["УК-акция"] == {"logo": "sh1.png", "logo_color": "#AA0000", "source": "table", "group": False, "file": None}
    assert uk["УК-фонд"]["logo"] == "e3.png" and uk["УК-фонд"]["logo_color"] == "#00BB00"
    assert uk["УК-файл"]["logo"] == "x.png" and uk["УК-файл"]["logo_color"] == "#112233"
    assert uk["УК-файл-цвет"]["logo_color"] == "#ABCDEF"
    assert uk["УК-эмитент"] == {"logo": "iss.png", "logo_color": "#0000CC", "source": "table", "group": True, "file": None}
    assert uk["УК-нет"] == {"logo": None, "logo_color": None, "source": None, "group": False, "file": None}
    assert all(v["group"] is False for k, v in uk.items() if k != "УК-эмитент")


def test_bond_issuer_matches_exact_name_or_series_and_needs_single_logo(monkeypatch):
    sources = {"УК1": {"bond_issuer": "Банк"}, "УК2": {"bond_issuer": "Газ"}}
    funds = [_fund("F1", "УК1"), _fund("F2", "УК2")]
    bonds = [
        {"ticker": "A", "name": "Банк", "logo": "bank.png", "logo_color": "#010101"},
        {"ticker": "B", "name": "Банк 1P-01", "logo": "bank.png", "logo_color": "#010101"},
        {"ticker": "C", "name": "Банковский дом 2", "logo": "other.png", "logo_color": "#020202"},  # не граница слова
        {"ticker": "D", "name": "Газ 1", "logo": "g1.png", "logo_color": "#030303"},
        {"ticker": "E", "name": "Газ 2", "logo": "g2.png", "logo_color": "#040404"},                 # два логотипа
    ]
    uk = _build(monkeypatch, sources, funds, bonds=bonds)["uk"]
    assert uk["УК1"]["logo"] == "bank.png"
    assert uk["УК2"]["logo"] is None and uk["УК2"]["source"] is None


def test_common_logo_rule(monkeypatch):
    funds = [_fund("A1", "Общая"), _fund("A2", "Общая"), _fund("A3", "Общая"),
             _fund("B1", "Разные"), _fund("B2", "Разные"),
             _fund("C1", "Одна"),                            # одна иконка на УК -> иконка продукта, а не УК
             _fund("D1", "БезСправочника"), _fund("D2", "БезСправочника")]
    etfs = [_etf("A1", "uk.png", "#123456"), _etf("A2", "uk.png", "#123456"), _etf("A3", "prod.png"),
            _etf("B1", "p1.png"), _etf("B2", "p2.png"),
            _etf("C1", "solo.png")]
    body = _build(monkeypatch, {}, funds, etfs)
    uk = body["uk"]
    assert uk["Общая"] == {"logo": "uk.png", "logo_color": "#123456", "source": "common", "group": False, "file": None}
    for name in ("Разные", "Одна", "БезСправочника"):
        assert uk[name]["logo"] is None and uk[name]["logo_color"] is None and uk[name]["source"] is None, name
    # фонд без записи в справочнике T-Invest всё равно сопоставлен УК
    assert body["funds"]["D1"] == "БезСправочника" and body["funds"]["A3"] == "Общая"
    assert list(body["funds"]) == sorted(body["funds"]) and list(uk) == sorted(uk)


def test_common_logo_without_logo_records_is_ignored(monkeypatch):
    funds = [_fund("A1", "У"), _fund("A2", "У")]
    uk = _build(monkeypatch, {}, funds, [_etf("A1"), _etf("A2")])["uk"]
    assert uk["У"]["source"] is None


def test_rows_without_ticker_or_issuer_are_skipped(monkeypatch):
    body = _build(monkeypatch, {}, [{"ticker": "X", "issuer": None}, {"ticker": None, "issuer": "У"}, _fund("Y", "У")])
    assert body["funds"] == {"Y": "У"} and list(body["uk"]) == ["У"]


def test_file_found_in_web_dir_svg_has_priority(monkeypatch, tmp_path):
    d = tmp_path / ul.FILES_DIR
    d.mkdir(parents=True)
    (d / "alfa-kapital.png").write_bytes(b"x")
    (d / "alfa-kapital.svg").write_bytes(b"<svg/>")
    (d / "aaa.png").write_bytes(b"x")
    (d / "bks.svg").mkdir()                                   # каталог — не файл
    funds = [_fund("1", "Альфа Капитал"), _fund("2", "ААА"), _fund("3", "БКС"), _fund("4", "Финам")]
    uk = _build(monkeypatch, {}, funds, web_dir=tmp_path)["uk"]
    assert uk["Альфа Капитал"]["file"] == "img/uk/alfa-kapital.svg"
    assert uk["ААА"]["file"] == "img/uk/aaa.png"
    assert uk["БКС"]["file"] is None and uk["Финам"]["file"] is None


def test_file_none_without_web_dir(monkeypatch):
    assert _build(monkeypatch, {}, [_fund("1", "Альфа Капитал")])["uk"]["Альфа Капитал"]["file"] is None


# ---------------------------------------------------------------- build: bonds_issuer
def _tb(ticker, name, logo="iss.png", color="#0a0a0a"):
    return {"ticker": ticker, "name": name, "logo": logo, "logo_color": color if logo else None}


def _sb(secid, name):
    return {"secid": secid, "name": name}


def _issuer(monkeypatch, tinvest, screener):
    return _build(monkeypatch, {}, [], bonds=tinvest, screener=screener)["bonds_issuer"]


def test_bonds_issuer_added_for_two_confirming_bonds_and_one_logo(monkeypatch):
    tinvest = [_tb("A1", "ВТБ Б1-243"), _tb("A2", "ВТБ Б1-295", color="#0a0a0a")]
    screener = [_sb("A1", "ВТБ Б1-243"), _sb("A2", "ВТБ Б1-295"), _sb("A3", "ВТБ Б1-300"), _sb("A4", "ВТБ Б2-7")]
    assert _issuer(monkeypatch, tinvest, screener) == {
        "A3": {"logo": "iss.png", "logo_color": "#0a0a0a"}, "A4": {"logo": "iss.png", "logo_color": "#0a0a0a"}}


def test_bonds_issuer_not_added_when_two_different_logos(monkeypatch):
    tinvest = [_tb("A1", "Газ 1", "g1.png"), _tb("A2", "Газ 2", "g2.png")]
    screener = [_sb("A1", "Газ 1"), _sb("A2", "Газ 2"), _sb("A3", "Газ 3")]
    assert _issuer(monkeypatch, tinvest, screener) == {}


def test_bonds_issuer_not_added_with_single_confirming_bond(monkeypatch):
    tinvest = [_tb("A1", "Один 1")]
    screener = [_sb("A1", "Один 1"), _sb("A2", "Один 2")]
    assert _issuer(monkeypatch, tinvest, screener) == {}


def test_bonds_issuer_not_added_for_short_key(monkeypatch):
    tinvest = [_tb("A1", "АБ1"), _tb("A2", "АБ2")]
    screener = [_sb("A1", "АБ1"), _sb("A2", "АБ2"), _sb("A3", "АБ3")]
    assert ul.issuer_key("АБ3") == "АБ" and _issuer(monkeypatch, tinvest, screener) == {}


def test_bonds_issuer_skips_secid_already_in_tinvest_even_without_logo(monkeypatch):
    tinvest = [_tb("A1", "Эмит 1"), _tb("A2", "Эмит 2"), _tb("A3", "Эмит 3", logo=None)]
    screener = [_sb("A1", "Эмит 1"), _sb("A2", "Эмит 2"), _sb("A3", "Эмит 3"), _sb("A4", "Эмит 4")]
    out = _issuer(monkeypatch, tinvest, screener)
    assert set(out) == {"A4"}                                   # A3 есть в tinvest_bonds -> не перекрываем


def test_bonds_issuer_empty_without_screener(monkeypatch):
    assert _issuer(monkeypatch, [_tb("A1", "Эмит 1")], None) == {}
    assert _issuer(monkeypatch, [_tb("A1", "Эмит 1")], []) == {}


# ---------------------------------------------------------------- шаг сборщика
def _write_sources(out: Path, n=25, uk="Общая УК"):
    def dump(name, rows):
        (out / f"{name}.json").write_text(json.dumps({"updated": "x", "data": rows}, ensure_ascii=False), encoding="utf-8")
    dump("rusetfs_funds", [_fund(f"T{i}", uk) for i in range(n)])
    dump("tinvest_etfs", [_etf(f"T{i}", "uk.png", "#123456") for i in range(n)])
    dump("tinvest_shares", [{"ticker": f"S{i}", "name": "s", "logo": None} for i in range(n)])
    dump("tinvest_bonds", [_tb("A1", "Эмит 1"), _tb("A2", "Эмит 2")])
    dump("screener_bonds", [_sb("A1", "Эмит 1"), _sb("A2", "Эмит 2"), _sb("A3", "Эмит 3")])


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    return tmp_path


def _read(out, name):
    return json.loads((out / f"{name}.json").read_text(encoding="utf-8"))


def test_uk_logos_step_is_not_default_and_runs_after_tinvest_bonds():
    assert cd.SOURCES["uk_logos"]["default"] is False
    assert [n for n in cd.SOURCES if n in ("tinvest_bonds", "uk_logos")] == ["tinvest_bonds", "uk_logos"]


def test_default_run_does_not_include_uk_logos(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    ran = []
    monkeypatch.setattr(cd, "collect_uk_logos", lambda *a: ran.append(1) or {"ok": True, "rows": 1, "last": "x"})
    for n, s in list(cd.SOURCES.items()):
        monkeypatch.setitem(cd.SOURCES, n, {**s, "default": False} if n != "uk_logos" else s)
    cd.main([])
    assert not ran


def test_uk_logos_step_writes_file(out):
    _write_sources(out)
    assert cd.main(["--only", "uk_logos"]) == 0
    d = _read(out, "uk_logos")
    assert list(d) == ["updated", "source", "funds", "uk", "bonds_issuer"] and d["source"] == ul.SOURCE
    assert d["uk"]["Общая УК"]["logo"] == "uk.png" and d["uk"]["Общая УК"]["source"] == "common"
    assert d["bonds_issuer"] == {"A3": {"logo": "iss.png", "logo_color": "#0a0a0a"}}
    st = _read(out, "status")["uk_logos"]
    assert st["ok"] and st["rows"] == 1 and st["with_logo"] == 1 and st["bonds_issuer"] == 1
    assert not list(out.glob(".*.tmp"))


def test_uk_logos_updated_constant_for_same_content_and_changes_with_content(out, monkeypatch):
    _write_sources(out)
    stamps = iter(f"2026-01-0{i}T00:00:00+00:00" for i in range(1, 9))
    monkeypatch.setattr(cd, "_now", lambda: next(stamps))
    cd.main(["--only", "uk_logos"])
    first = _read(out, "uk_logos")
    cd.main(["--only", "uk_logos"])
    assert _read(out, "uk_logos") == first, "при том же содержимом файл (включая updated) не меняется"
    _write_sources(out, uk="Другая УК")
    cd.main(["--only", "uk_logos"])
    third = _read(out, "uk_logos")
    assert third["updated"] != first["updated"] and "Другая УК" in third["uk"]


@pytest.mark.parametrize("broken", ["rusetfs_funds", "tinvest_etfs", "tinvest_shares"])
def test_uk_logos_step_failure_keeps_previous_file(out, broken):
    _write_sources(out)
    cd.main(["--only", "uk_logos"])
    before = (out / "uk_logos.json").read_bytes()
    _write_sources(out)
    (out / f"{broken}.json").write_text(json.dumps({"data": [_fund("T0", "У")]}), encoding="utf-8")   # мало строк
    assert cd.main(["--only", "uk_logos"]) == 1
    assert (out / "uk_logos.json").read_bytes() == before
    st = _read(out, "status")["uk_logos"]
    assert not st["ok"] and "слишком мало" in st["error"] and st["last"]


def test_uk_logos_step_missing_source_file_is_failure_and_writes_nothing(out):
    assert cd.main(["--only", "uk_logos"]) == 1
    assert not (out / "uk_logos.json").exists()


# ---------------------------------------------------------------- public/data/uk_logos.json
@pytest.fixture(scope="module")
def pub():
    return json.loads((DATA / "uk_logos.json").read_text(encoding="utf-8"))


def test_public_uk_logos_structure(pub):
    assert {"updated", "source", "funds", "uk", "bonds_issuer"} <= set(pub) and pub["source"] and pub["updated"]
    rus = json.loads((DATA / "rusetfs_funds.json").read_text(encoding="utf-8"))["data"]
    tickers = {r["ticker"] for r in rus}
    assert len(tickers) == len(rus) == 214, "в RusETFs 214 фондов — обновите число, если справочник вырос"
    assert set(pub["funds"]) == tickers, "все тикеры RusETFs должны быть в funds"
    assert {r["ticker"]: r["issuer"] for r in rus} == pub["funds"]
    assert set(pub["funds"].values()) == set(pub["uk"]), "у каждой УК фондов есть запись, лишних записей нет"


def test_public_uk_logos_every_uk_entry(pub):
    color = re.compile(r"^#[0-9a-fA-F]{6}$")
    for name, e in pub["uk"].items():
        assert set(e) == {"logo", "logo_color", "source", "group", "file"}, name
        assert isinstance(e["group"], bool)
        assert e["source"] in ("table", "common", None), name
        if e["logo"]:
            assert e["logo"].endswith(".png") and color.match(e["logo_color"]) and e["source"], name
        else:
            assert e["logo_color"] is None and e["source"] is None and e["group"] is False, name
        if e["group"]:
            assert e["source"] == "table", name
        if e["file"]:
            assert e["file"] == f"img/uk/{ul.slug(name)}.{e['file'].rsplit('.', 1)[-1]}"
            assert (config.ROOT / "web" / e["file"]).is_file(), name
        else:
            assert not any((config.ROOT / "web" / ul.FILES_DIR / f"{ul.slug(name)}.{x}").is_file() for x in ("svg", "png")), \
                f"{name}: файл логотипа лежит в web/img/uk, но file = null — запустите сборщик"


def test_public_uk_logos_table_sources_match_code(pub):
    for name, src in ul.UK_LOGO_SOURCES.items():
        if name in pub["uk"]:
            assert pub["uk"][name]["group"] is bool(src.get("group")), name
    for name, e in pub["uk"].items():
        if e["source"] == "table":
            assert name in ul.UK_LOGO_SOURCES, name
        if e["source"] == "common":
            assert name not in ul.UK_LOGO_SOURCES, name


def test_public_bonds_issuer_does_not_overlap_tinvest_bonds(pub):
    color = re.compile(r"^#[0-9a-fA-F]{6}$")
    have = {r["ticker"] for r in json.loads((DATA / "tinvest_bonds.json").read_text(encoding="utf-8"))["data"]}
    screener = {r["secid"] for r in json.loads((DATA / "screener_bonds.json").read_text(encoding="utf-8"))["data"]}
    bi = pub["bonds_issuer"]
    assert bi and not (set(bi) & have) and set(bi) <= screener
    assert all(set(v) == {"logo", "logo_color"} and v["logo"].endswith(".png") and color.match(v["logo_color"]) for v in bi.values())
