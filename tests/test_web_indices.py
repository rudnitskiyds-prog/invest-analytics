"""Веб: раздел «Индексы», полоса на главной, логотипы по правилу, ровная вёрстка (web/CONTRACT.md,
«Индексы, логотипы по правилу, ровная вёрстка»). Демо-режим, сеть заблокирована как в test_web_smoke.Session;
файлы public/data подменяются через page.route (позже зарегистрированный маршрут срабатывает раньше)."""
from __future__ import annotations

import base64
import json
from urllib.parse import quote, unquote

import pytest

from tests.test_web_smoke import (IDLE, TIMEOUT, Session, browser, demo, server)  # noqa: F401  (фикстуры)
from tests.test_web_smoke import ROOT

INDICES = json.loads((ROOT / "public" / "data" / "indices.json").read_text(encoding="utf-8"))
ITEMS = INDICES["items"]
CDN = "https://invest-brands.cdn-tinkoff.ru/"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _json_route(page, name, body, status=200):
    page.route(f"**/public/data/{name}.json", lambda r: r.fulfill(
        status=status, content_type="application/json" if status == 200 else "text/plain",
        body=json.dumps(body, ensure_ascii=False) if status == 200 else "not found"))


def _cdn(page):
    """Логотипы Т-Банка отдаются заглушкой-PNG (сеть закрыта); возвращает список запрошенных адресов."""
    seen: list[str] = []
    page.route(CDN + "**", lambda r: (seen.append(r.request.url), r.fulfill(status=200, content_type="image/png", body=PNG))[1])
    return seen


def _open(demo, hash_):
    demo.goto("?demo=1", hash_)
    return demo.page


def _cards(page):
    return page.evaluate("""() => [...document.querySelectorAll('#indices-root .idx-card')].map((c) => ({
        href: c.querySelector('a.idx-link').getAttribute('href'),
        sparks: c.querySelectorAll('svg.idx-spark path').length,
        imgs: c.querySelectorAll('.idx-top img').length,
        basket: !!c.querySelector('.idx-top .asset-badge.is-basket'),
        ret: (c.querySelector('.idx-ret-val') || {}).textContent || '',
        repeat: !!c.querySelector('.idx-repeat'),
        funds: [...c.querySelectorAll('.idx-repeat a.idx-fund b')].map((b) => b.textContent),
        fundLinks: [...c.querySelectorAll('.idx-repeat a.idx-fund')].map((a) => a.getAttribute('href')),
        group: (c.closest('.idx-group') || {dataset: {}}).dataset.idx || null}))""")


# ================================================================ #/indices
def test_indices_page_groups_and_cards(demo):
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-indices"]
    assert page.locator("a[data-nav=indices]").count() == 0, "пункта «Индексы» в верхнем меню больше нет"
    assert page.locator("#nav-sub-btn[aria-current=page]").count() == 1
    assert page.locator("#nav-screeners a[href='#/screener/indices']").inner_text() == "Индексы"
    tabs = page.evaluate("() => [...document.querySelectorAll('#indices-root .scr-tab')].map((a) => [a.textContent, a.getAttribute('aria-current')])")
    assert tabs == [["Акции", None], ["Фонды", None], ["Облигации", None], ["Индексы", "page"]], tabs
    assert page.locator("#indices-root h2").inner_text() == "Индексы"
    assert INDICES["as_of"][:4] in page.locator("#indices-root .idx-meta").inner_text() or "Данные на" in page.locator("#indices-root .idx-meta").inner_text()
    groups = page.evaluate("() => [...document.querySelectorAll('#indices-root .idx-group')].map((g) => g.getAttribute('aria-label'))")
    assert len(groups) >= 4, groups
    cards = _cards(page)
    assert len(cards) == len(ITEMS)
    by = {i["key"]: i for i in ITEMS}
    for c in cards:
        key = unquote(c["href"].removeprefix("#/symbol/"))
        assert c["href"] == f"#/symbol/{quote(key, safe='')}" and key in by, c
        assert c["sparks"] == 1, f"{key}: нет спарклайна"
        assert "%" in c["ret"], f"{key}: нет доходности за год: {c['ret']!r}"
        assert c["imgs"] == 0 and c["basket"], f"{key}: у индекса только значок-корзина, без <img>"
        etf = by[key]["etf"]
        assert c["repeat"] == bool(etf), key
        if etf:
            assert c["funds"] == [f["secid"] for f in etf][:3] and [unquote(x) for x in c["fundLinks"]] == [f"#/symbol/{f['secid']}" for f in etf][:3]
    assert any(not by[k]["etf"] for k in by), "в демо-файле должен быть индекс без БПИФ"
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    assert not page.evaluate("() => /\\b(NaN|undefined|null|Infinity)\\b/.test(document.querySelector('#indices-root').innerText)")
    demo.check_clean()


def test_old_indices_route_redirects(demo):
    page = _open(demo, "#/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    assert page.evaluate("() => location.hash") == "#/screener/indices"
    assert demo.visible_panel() == ["panel-indices"]
    demo.check_clean()


def test_indices_card_opens_symbol(demo):
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    key = ITEMS[0]["key"]
    page.locator(f"#indices-root a.idx-link[href='#/symbol/{quote(key, safe='')}']").click()
    page.wait_for_function(f"() => location.hash === '#/symbol/{quote(key, safe='')}' && !document.querySelector('#panel-symbol').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-symbol"]
    demo.check_clean()


def test_indices_sort_toggle_changes_order(demo):
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    keys = lambda: [unquote(c["href"].removeprefix("#/symbol/")) for c in _cards(page)]  # noqa: E731
    pressed = lambda: page.evaluate("() => document.querySelector('.idx-sort-btn[aria-pressed=true]').textContent")  # noqa: E731
    assert pressed() == "По классам"
    by_class = keys()
    order = ["Акции", "Облигации гос.", "Облигации корп.", "Золото", "Денежный рынок"]
    cls = {i["key"]: order.index(i["class"]) for i in ITEMS}
    assert [cls[k] for k in by_class] == sorted(cls[k] for k in by_class), "группы идут в порядке классов"
    page.get_by_role("button", name="По доходности за год").click()
    page.wait_for_function("() => !document.querySelector('#indices-root .idx-group')", timeout=TIMEOUT)
    assert pressed() == "По доходности за год"
    by_ret = keys()
    ret = {i["key"]: i["ret_1y"] for i in ITEMS}
    assert by_ret != by_class and sorted(by_ret) == sorted(by_class)
    vals = [ret[k] if ret[k] is not None else float("-inf") for k in by_ret]
    assert vals == sorted(vals, reverse=True), "по убыванию доходности за год"
    page.get_by_role("button", name="По классам").click()
    page.wait_for_selector("#indices-root .idx-group", timeout=TIMEOUT)
    assert keys() == by_class
    demo.check_clean()


def test_indices_empty_state_when_file_missing(demo):
    _json_route(demo.page, "indices", None, status=404)
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .notice", timeout=TIMEOUT)
    assert page.locator("#indices-root .idx-card").count() == 0
    assert "Данных по индексам пока нет" in page.locator("#indices-root").inner_text()
    assert not [e for e in demo.errors if e.startswith("pageerror")], demo.errors
    demo.check_clean(allow=(r"404.*indices\.json",))


def test_indices_empty_items_gives_empty_state(demo):
    _json_route(demo.page, "indices", {"updated": "x", "source": "s", "as_of": "2025-12-31", "items": []})
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .notice", timeout=TIMEOUT)
    demo.check_clean()


def test_indices_item_with_nulls_renders_dashes(demo):
    it = {**ITEMS[0], "ret_1y": None, "cagr_5y": None, "cagr_10y": None, "max_drawdown": None, "volatility_1y": None,
          "etf": [], "series": [["2025-01-31", 100]]}
    _json_route(demo.page, "indices", {**INDICES, "items": [it]})
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    text = page.locator("#indices-root .idx-card").inner_text()
    assert "—" in text and "NaN" not in text and "undefined" not in text and "null" not in text
    assert page.locator("#indices-root .idx-repeat").count() == 0
    demo.check_clean()


# ================================================================ главная
def test_home_indices_strip(demo):
    page = _open(demo, "")
    page.wait_for_selector("#home-indices li", timeout=TIMEOUT)
    n = page.locator("#home-indices li").count()
    assert 5 <= n <= 6, n
    hrefs = page.evaluate("() => [...document.querySelectorAll('#home-indices a.idx-card')].map((a) => a.getAttribute('href'))")
    keys = [unquote(h.removeprefix("#/symbol/")) for h in hrefs]
    assert set(keys) <= {i["key"] for i in ITEMS} and len(set(keys)) == n
    assert page.locator("#home-indices svg.idx-spark").count() == n
    assert page.locator("#home-indices .idx-top img").count() == 0
    more = page.locator("#home-idx a.more")
    assert more.inner_text().startswith("Все индексы") and more.get_attribute("href") == "#/screener/indices"
    # полоса — после «Популярных бумаг»
    assert page.evaluate("() => document.querySelector('#home-popular').closest('section').nextElementSibling.id") == "home-idx"
    more.click()
    page.wait_for_function("() => location.hash === '#/screener/indices' && !document.querySelector('#panel-indices').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-indices"]
    demo.check_clean()


def test_home_popular_has_no_indices(demo):
    page = _open(demo, "")
    page.wait_for_selector("#home-popular a.pop-card", timeout=TIMEOUT)
    keys = page.evaluate("() => [...document.querySelectorAll('#home-popular .pop-key')].map((e) => e.textContent.trim())")
    index_keys = {i["key"] for i in ITEMS} | {"IMOEX", "MCFTR", "RGBITR"}
    assert keys and not (set(keys) & index_keys), keys
    chips = page.evaluate("() => [...document.querySelectorAll('#home-popular .chip-cls')].map((e) => e.textContent.trim())")
    assert "Индекс" not in chips, chips
    demo.check_clean()


def test_home_without_indices_file_hides_strip(demo):
    _json_route(demo.page, "indices", None, status=404)
    page = _open(demo, "")
    page.wait_for_selector("#home-popular a.pop-card", timeout=TIMEOUT)
    page.wait_for_timeout(300)
    assert page.locator("#home-idx").is_hidden()
    assert not [e for e in demo.errors if e.startswith("pageerror")], demo.errors
    demo.check_clean(allow=(r"404.*indices\.json",))


# ================================================================ поиск
def test_search_index_suggestion_has_badge(demo):
    page = _open(demo, "")
    page.locator("#nav-search").fill("IMOEX")
    page.wait_for_selector(".search-list:not([hidden]) .search-opt", timeout=TIMEOUT)
    opt = page.locator(".search-opt", has_text="IMOEX").first
    badge = opt.locator(".so-cls.so-idx")
    assert badge.inner_text().startswith("Индекс")
    assert opt.locator("img").count() == 0 and opt.locator(".asset-badge.is-basket").count() == 1
    demo.check_clean()


# ================================================================ логотипы по правилу
def _scr(page, kind, query):
    page.evaluate(f"() => {{ location.hash = '#/screener/{kind}'; }}")
    page.wait_for_selector("#screener-root .scr-search input", timeout=TIMEOUT)
    page.locator("#screener-root .scr-search input").fill(query)
    page.wait_for_function("() => document.querySelectorAll('#screener-root tbody tr').length >= 1", timeout=TIMEOUT)


BADGE_JS = """(sel) => [...document.querySelectorAll(sel)].map((tr) => {
    const b = tr.querySelector('.asset-badge'); const img = b && b.querySelector('img');
    return {text: tr.innerText, badge: !!b, img: img ? img.getAttribute('src') : null,
            initials: b ? b.dataset.initials || null : null, title: b ? b.title : null,
            issuerImg: (tr.querySelector('.scr-issuer img') || {}).src || null,
            issuerText: (tr.querySelector('.scr-issuer') || {}).innerText || null,
            issuerInitials: (tr.querySelector('.scr-issuer .asset-badge') || {dataset: {}}).dataset.initials || null}; })"""


def test_screener_stocks_issuer_logo(demo):
    page = demo.page
    seen = _cdn(page)
    _json_route(page, "tinvest_shares", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "ZZLOGO", "name": "Эмитент с лого", "logo": "zzlogo.png", "logo_color": "#21a038", "brand": "Эмитент"},
        {"ticker": "ZZPLAIN", "name": "Эмитент без лого"}]})
    _open(demo, "#/screener/stocks")
    _scr(page, "stocks", "ZZ")
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    assert rows["ZZLOGO"]["img"] == CDN + "zzlogox160.png", rows["ZZLOGO"]
    assert rows["ZZPLAIN"]["img"] is None and rows["ZZPLAIN"]["initials"] == "ZZ", rows["ZZPLAIN"]
    assert CDN + "zzlogox160.png" in seen
    demo.check_clean()


def test_screener_bonds_issuer_logo(demo):
    page = demo.page
    _cdn(page)
    _json_route(page, "screener_bonds", {"updated": "2026-11-27", "data": [
        {"secid": "RUZZ001", "name": "Облигация с лого", "board": "TQCB", "yield": 0.15, "price_pct": 99},
        {"secid": "RUZZ002", "name": "Облигация без лого", "board": "TQOB", "yield": 0.14, "price_pct": 98}]})
    _json_route(page, "tinvest_bonds", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "RUZZ001", "isin": "RU1", "name": "Облигация с лого", "logo": "issuer1.png",
         "logo_color": "#123456", "brand": "Эмитент Один"}]})
    _open(demo, "#/screener/bonds")
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    assert rows["RUZZ001"]["img"] == CDN + "issuer1x160.png" and rows["RUZZ001"]["title"] == "Эмитент Один", rows
    # у облигации без логотипа эмитента — значок-«купон» (ICONS.bond в logo.js), а не <img>
    assert rows["RUZZ002"]["img"] is None and rows["RUZZ002"]["badge"], rows["RUZZ002"]
    assert page.locator("#screener-root tbody tr", has_text="RUZZ002").locator(".asset-badge.is-icon svg").count() == 1
    demo.check_clean()


def _uk_json(page, funds, uk, bonds_issuer=None):
    _json_route(page, "uk_logos", {"updated": "x", "source": "t", "funds": funds, "uk": uk,
                                   "bonds_issuer": bonds_issuer or {}})


def _uk(logo=None, color=None, file=None):
    return {"logo": logo, "logo_color": color, "source": "table" if logo else None, "group": False, "file": file}


def _funds_routes(page):
    seen = _cdn(page)
    # иконки продуктов в tinvest_etfs РАЗНЫЕ — для фондов они не используются, только логотип УК
    _json_route(page, "tinvest_etfs", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "ZZFUND", "name": "Фонд 1", "logo": "product1.png", "logo_color": "#0a0a0a", "brand": "Продукт 1"},
        {"ticker": "ZZFUN2", "name": "Фонд 2", "logo": "product2.png", "logo_color": "#0b0b0b", "brand": "Продукт 2"},
        {"ticker": "ZZNOLG", "name": "Фонд без лого УК", "logo": "product3.png", "brand": "Продукт 3"}]})
    _json_route(page, "rusetfs_funds", {"updated": "x", "source": "RusETFs", "data": [
        {"ticker": t, "name": n, "issuer": u, "asset_class": "Акции", "commission_pct": 1.0}
        for t, n, u in (("ZZFUND", "Фонд 1", "Тест УК"), ("ZZFUN2", "Фонд 2", "Тест УК"), ("ZZNOLG", "Фонд без лого УК", "Безлогова Компания"))]})
    _uk_json(page, {"ZZFUND": "Тест УК", "ZZFUN2": "Тест УК", "ZZNOLG": "Безлогова Компания"},
             {"Тест УК": _uk("ukone.png", "#123456"), "Безлогова Компания": _uk()})
    return seen


def test_screener_funds_logo_is_management_company_logo(demo):
    page = demo.page
    seen = _funds_routes(page)
    _open(demo, "#/screener/funds")
    _scr(page, "funds", "ZZ")
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    a, a2, b = rows["ZZFUND"], rows["ZZFUN2"], rows["ZZNOLG"]
    assert a["img"] == a2["img"] == CDN + "ukonex160.png", (a, a2)       # у фондов одной УК один логотип
    assert a["issuerImg"] == a2["issuerImg"] == CDN + "ukonex160.png"
    assert a["title"] == "Тест УК" and "Тест УК" in a["issuerText"], a
    # УК без логотипа — монограмма УК («БК»), а не иконка продукта
    assert b["img"] is None and b["initials"] == "БК" and b["title"] == "Безлогова Компания", b
    assert b["issuerImg"] is None and b["issuerInitials"] == "БК", b
    assert not [u for u in seen if "product" in u], f"иконки продуктов фондов не должны запрашиваться: {seen}"
    demo.check_clean()


def test_uk_file_has_priority_over_cdn_logo(demo):
    """CONTRACT C: приоритет file -> CDN logo -> монограмма (uk[УК].file — путь относительно web/)."""
    page = demo.page
    _funds_routes(page)
    _uk_json(page, {"ZZFUND": "Тест УК", "ZZFUN2": "Тест УК", "ZZNOLG": "Безлогова Компания"},
             {"Тест УК": _uk("ukone.png", "#123456", file="img/uk/test-uk.png"),
              "Безлогова Компания": _uk(file="img/uk/bezlogova-kompaniya.svg")})
    served = []
    page.route("**/web/img/uk/*", lambda r: (served.append(r.request.url), r.fulfill(status=200, content_type="image/png", body=PNG))[1])
    _open(demo, "#/screener/funds")
    _scr(page, "funds", "ZZ")
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    assert (rows["ZZFUND"]["img"] or "").endswith("img/uk/test-uk.png"), rows["ZZFUND"]
    assert (rows["ZZNOLG"]["img"] or "").endswith("img/uk/bezlogova-kompaniya.svg"), rows["ZZNOLG"]


def test_uk_file_from_json_must_be_local_logo_path(demo):
    """uk_logos.json: file вне img/uk/*.svg|png (чужой URL) в src не попадает — откат к CDN-логотипу или монограмме."""
    page = demo.page
    _funds_routes(page)
    _uk_json(page, {"ZZFUND": "Тест УК", "ZZFUN2": "Тест УК", "ZZNOLG": "Безлогова Компания"},
             {"Тест УК": _uk("ukone.png", "#123456", file="https://evil/x"),
              "Безлогова Компания": _uk(file="//evil/x.png")})
    _open(demo, "#/screener/funds")
    _scr(page, "funds", "ZZ")
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    assert rows["ZZFUND"]["img"] == CDN + "ukonex160.png", rows["ZZFUND"]
    assert rows["ZZNOLG"]["img"] is None and rows["ZZNOLG"]["initials"] == "БК", rows["ZZNOLG"]
    assert not page.evaluate("() => [...document.images].some((i) => i.src.includes('evil'))")


# ================================================================ вёрстка
def _layout(browser, server, width):
    s = Session(browser, server, live=False, width=width)
    return s


def _bt_fields(page):
    return page.evaluate("""() => [...document.querySelectorAll('#bt-form .form-grid .field')].map((f) => {
        const r = f.getBoundingClientRect(); const c = f.querySelector('input, select').getBoundingClientRect();
        return {w: r.width, h: r.height, left: r.left, top: r.top, ch: c.height}; })""")


def test_backtest_params_grid_1440(browser, server):
    s = _layout(browser, server, 1440)
    try:
        s.goto("?demo=1", "#/tools/backtest")
        s.page.wait_for_selector("#bt-form .form-grid .field", timeout=TIMEOUT)
        f = _bt_fields(s.page)
        assert len(f) == 9
        assert max(x["w"] for x in f) - min(x["w"] for x in f) < 1, [x["w"] for x in f]
        assert all(abs(x["ch"] - 44) < 0.5 for x in f), [x["ch"] for x in f]
        assert max(x["h"] for x in f) - min(x["h"] for x in f) < 1, [x["h"] for x in f]
        assert len({round(x["left"]) for x in f}) == 3, "3 колонки на 1440"
        s.check_clean()
    finally:
        s.close()


def test_backtest_params_grid_390(browser, server):
    s = _layout(browser, server, 390)
    try:
        s.goto("?demo=1", "#/tools/backtest")
        s.page.wait_for_selector("#bt-form .form-grid .field", timeout=TIMEOUT)
        f = _bt_fields(s.page)
        assert len(f) == 9
        assert len({round(x["left"]) for x in f}) == 1, "одна колонка на 390"
        assert max(x["w"] for x in f) - min(x["w"] for x in f) < 1
        assert all(abs(x["ch"] - 44) < 0.5 for x in f)
        assert s.page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        s.check_clean()
    finally:
        s.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_home_headings_left_aligned(browser, server, width):
    s = _layout(browser, server, width)
    try:
        s.goto("?demo=1")
        s.page.wait_for_selector("#home-indices li", timeout=TIMEOUT)
        info = s.page.evaluate("""() => {
            const vis = (e) => e.offsetParent !== null;
            const t = [...document.querySelectorAll('#panel-home .block-title')].filter(vis);
            const heads = [...document.querySelectorAll('#panel-home [class*="block-head"]')].filter(vis);
            return {titles: t.map((e) => ({id: e.id, align: getComputedStyle(e).textAlign, left: e.getBoundingClientRect().left})),
                    heads: heads.map((e) => ({cls: e.className, align: getComputedStyle(e).textAlign}))}; }""")
        assert len(info["titles"]) >= 5 and info["heads"]
        assert all(x["align"] in ("left", "start") for x in info["titles"] + info["heads"]), info
        lefts = [round(x["left"]) for x in info["titles"]]
        assert max(lefts) - min(lefts) <= 1, f"заголовки секций не на одной вертикали: {info['titles']}"
        s.check_clean()
    finally:
        s.close()


def test_indices_no_horizontal_scroll_375(browser, server):
    s = _layout(browser, server, 375)
    try:
        s.goto("?demo=1", "#/screener/indices")
        s.page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
        assert s.page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"), \
            s.page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        w = s.page.evaluate("() => [...document.querySelectorAll('#indices-root .idx-card')].map((c) => c.getBoundingClientRect().right)")
        assert max(w) <= 375
        s.page.get_by_role("button", name="По доходности за год").click()
        assert s.page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        s.check_clean()
    finally:
        s.close()


def test_indices_card_labels_and_notes(demo):
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    assert page.locator("#indices-root").inner_text().count("Как повторить") == 0
    assert page.locator(".idx-repeat-lbl").first.inner_text() == "Похожие БПИФ"
    n = page.locator("#indices-root .idx-card").count()
    assert page.locator(".idx-risk small", has_text="(по мес. данным)").count() == 2 * n
    with_note = [i for i in ITEMS if i.get("etf_note")]
    assert page.locator(".idx-note").count() == len(with_note)
    assert all("etf_note" not in i for i in ITEMS if not i["etf"])
    demo.check_clean()


def test_indices_sort_button_keeps_focus(demo):
    page = _open(demo, "#/screener/indices")
    page.wait_for_selector("#indices-root .idx-card", timeout=TIMEOUT)
    btn = page.get_by_role("button", name="По доходности за год")
    btn.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("() => !document.querySelector('#indices-root .idx-group')", timeout=TIMEOUT)
    assert page.evaluate("() => document.activeElement.textContent") == "По доходности за год"
    assert page.evaluate("() => document.activeElement.getAttribute('aria-pressed')") == "true"
    demo.check_clean()


def _bond_logo_routes(page):
    seen = _cdn(page)
    _json_route(page, "tinvest_bonds", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "SU26238RMFS4", "isin": "RU000A1038V6", "name": "ОФЗ 26238", "logo": "minfin.png", "brand": "Минфин"}]})
    return seen


def test_home_popular_bond_issuer_logo(demo):
    page = demo.page
    seen = _bond_logo_routes(page)
    _open(demo, "")
    page.wait_for_selector("#home-popular a.pop-card", timeout=TIMEOUT)
    src = f"{CDN}minfinx160.png"
    page.wait_for_function("(s) => !!document.querySelector(`#home-popular a[href*=SU26238RMFS4] img[src='${s}']`)", arg=src, timeout=TIMEOUT)
    assert seen.count(src) >= 1
    demo.check_clean()


def test_search_bond_suggestion_has_issuer_logo(demo):
    page = demo.page
    _bond_logo_routes(page)
    _open(demo, "")
    page.locator("#nav-search").fill("SU26238")
    page.wait_for_selector(".search-list:not([hidden]) .search-opt", timeout=TIMEOUT)
    page.wait_for_function("(s) => !!document.querySelector(`.search-opt img[src='${s}']`)", arg=f"{CDN}minfinx160.png", timeout=TIMEOUT)
    demo.check_clean()


def test_bond_logo_joined_by_isin(demo):
    page = demo.page
    _cdn(page)
    _json_route(page, "screener_bonds", {"updated": "x", "data": [
        {"secid": "RUZZ777", "name": "Облигация", "board": "TQCB", "yield": 0.15, "price_pct": 99}]})
    _json_route(page, "tinvest_bonds", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "OTHER", "isin": "RUZZ777", "name": "Облигация", "logo": "byisin.png", "brand": "По ISIN"}]})
    _open(demo, "#/screener/bonds")
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    assert page.locator("#screener-root tbody tr img").first.get_attribute("src") == f"{CDN}byisinx160.png"
    demo.check_clean()


def test_bond_logo_falls_back_to_issuer_from_uk_logos_and_tinvest_wins(demo):
    """Облигация: tinvest_bonds, затем uk_logos.bonds_issuer, иначе значок-купон."""
    page = demo.page
    _cdn(page)
    _json_route(page, "screener_bonds", {"updated": "x", "data": [
        {"secid": "RUZZ001", "name": "Эмитент 1-01", "board": "TQCB", "yield": 0.15, "price_pct": 99},
        {"secid": "RUZZ002", "name": "Эмитент 1-02", "board": "TQCB", "yield": 0.15, "price_pct": 99},
        {"secid": "RUZZ003", "name": "Другой 1-01", "board": "TQCB", "yield": 0.15, "price_pct": 99}]})
    _json_route(page, "tinvest_bonds", {"updated": "x", "source": "T-Invest API", "data": [
        {"ticker": "RUZZ001", "isin": "RU1", "name": "Эмитент 1-01", "logo": "own.png", "brand": "Эмитент"}]})
    _uk_json(page, {}, {}, {"RUZZ001": {"logo": "issuer.png", "logo_color": "#010203"},
                            "RUZZ002": {"logo": "issuer.png", "logo_color": "#010203"}})
    _open(demo, "#/screener/bonds")
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    rows = {r["text"].split()[0]: r for r in page.evaluate(BADGE_JS, "#screener-root tbody tr")}
    assert rows["RUZZ001"]["img"] == CDN + "ownx160.png", rows["RUZZ001"]       # tinvest_bonds приоритетнее
    assert rows["RUZZ002"]["img"] == CDN + "issuerx160.png", rows["RUZZ002"]    # запасной — bonds_issuer
    assert rows["RUZZ003"]["img"] is None and rows["RUZZ003"]["badge"], rows["RUZZ003"]
    demo.check_clean()


def test_bonds_file_requested_once_per_session(demo):
    page = demo.page
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if r.url.endswith("/tinvest_bonds.json") else None)
    _open(demo, "")
    page.wait_for_selector("#home-popular a.pop-card", timeout=TIMEOUT)
    page.locator("#nav-search").fill("SU26238")
    page.wait_for_selector(".search-list:not([hidden]) .search-opt", timeout=TIMEOUT)
    page.wait_for_timeout(300)
    assert len(reqs) == 1, reqs
    demo.check_clean()
