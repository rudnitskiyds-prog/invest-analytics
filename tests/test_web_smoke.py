"""
Смоук-тесты веб-версии (web/): статический сервер из корня репозитория + Playwright (chromium).

Демо-режим (?demo=1): все запросы не к локальному серверу блокируются и считаются ошибкой.
Живой режим (страница бумаги, поиск, главная): запросы к iss.moex.com перехватываются page.route и
получают синтетические ответы tests/iss_fake.py; public/data — локальные файлы репозитория
(symbol_stats.json подставляется синтетический, кроме теста «файла нет»). Сеть не нужна.

Маршруты этапа 1 (web/CONTRACT.md): #/, #/tools, #/tools/backtest|frontier|metrics, #/docs, #/symbol/{TICKER},
#/screener/…, #/portfolios/lazy; старые ссылки ?tab=… перенаправляются.

Пропускается, если не установлен python-пакет playwright или браузер chromium.
"""
from __future__ import annotations

import functools
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests import iss_fake

ROOT = Path(__file__).resolve().parent.parent

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright не установлен")

IDLE = "() => !document.body.classList.contains('is-busy')"
TIMEOUT = 60_000
BAD_TEXT = re.compile(r"\b(NaN|undefined|null|Infinity)\b")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # без шума в выводе pytest
        pass


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # браузер не установлен — не наша зона
            pytest.skip(f"chromium для playwright недоступен: {e}")
        try:
            yield b
        finally:
            b.close()


class Session:
    """Страница с журналами: ошибки консоли, внешние запросы, незнакомые запросы к ISS."""

    def __init__(self, browser, server, *, live: bool, stats: bool = True, width: int | None = None):
        self.server = server
        kw = {"viewport": {"width": width, "height": 812}} if width else {}
        self.page = browser.new_page(**kw)
        self.errors: list[str] = []
        self.external: list[str] = []
        self.unknown_iss: list[str] = []
        self.iss_calls: list[str] = []
        self.page.on("console", lambda m: self.errors.append(f"console: {m.text} @ {m.location.get('url', '')}")
                     if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))

        def route(r):
            url = r.request.url
            if url.startswith(server):
                if urlparse(url).path.endswith("/public/data/symbol_stats.json"):
                    # файл от сборщика в репозитории не используется: синтетический или «файла нет» (404)
                    if stats:
                        r.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(iss_fake.symbol_stats(), ensure_ascii=False))
                    else:
                        r.fulfill(status=404, content_type="text/plain", body="not found")
                else:
                    r.continue_()
            elif live and urlparse(url).hostname == "iss.moex.com":
                self.iss_calls.append(url)
                res = iss_fake.respond(url)
                if res is None:
                    self.unknown_iss.append(url)
                    r.fulfill(status=404, body="not found", headers={"Access-Control-Allow-Origin": "*"})
                else:
                    status, body = res
                    r.fulfill(status=status, content_type="application/json", body=json.dumps(body),
                              headers={"Access-Control-Allow-Origin": "*"})
            else:
                self.external.append(url)
                r.abort()

        self.page.route("**/*", route)

    def goto(self, query: str = "", hash_: str = ""):
        self.page.goto(f"{self.server}/web/{query}{hash_}", wait_until="load", timeout=TIMEOUT)
        self.page.wait_for_function(IDLE, timeout=TIMEOUT)

    def hash(self) -> str:
        return self.page.evaluate("() => location.hash")

    def visible_panel(self) -> list[str]:
        return self.page.evaluate("() => [...document.querySelectorAll('.panel')].filter((p) => !p.hidden).map((p) => p.id)")

    def check_clean(self, allow=()):
        errs = [e for e in self.errors if not any(re.search(a, e) for a in allow)]
        assert not errs, "Ошибки в консоли:\n" + "\n".join(errs)
        assert not self.external, "Запросы во внешнюю сеть:\n" + "\n".join(self.external)
        assert not self.unknown_iss, "Незнакомые запросы к ISS (поддельный ISS их не знает):\n" + "\n".join(self.unknown_iss)

    def close(self):
        self.page.close()


@pytest.fixture
def demo(browser, server):
    s = Session(browser, server, live=False)
    yield s
    s.close()


@pytest.fixture
def live(browser, server):
    s = Session(browser, server, live=True)
    yield s
    s.close()


def _rerun(page, button_name: str, out: str):
    """Пометить текущий вывод (дочерние узлы out), нажать кнопку, дождаться перерисовки."""
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    page.evaluate(f"() => document.querySelectorAll('{out} > *')"
                  ".forEach((e) => e.setAttribute('data-stale', '1'))")
    page.get_by_role("button", name=button_name).click()
    page.wait_for_function(
        f"() => !document.body.classList.contains('is-busy') && "
        f"document.querySelectorAll('{out} > *').length > 0 && "
        f"!document.querySelector('{out} > [data-stale]')",
        timeout=TIMEOUT)


def _assert_table_and_chart(page, out: str, table_sel: str = "table"):
    assert page.locator(f"{out} {table_sel}").count() >= 1, f"{out}: нет таблицы показателей"
    rows = page.locator(f"{out} {table_sel} tbody tr").count()
    assert rows > 0, f"{out}: таблица пуста"
    assert page.locator(f"{out} canvas").count() >= 1, f"{out}: нет графика (canvas)"
    assert page.locator(f"{out} .notice-error, {out} [role=alert]").count() == 0, \
        page.locator(out).inner_text()[:2000]


def _current(page, attr: str) -> list[str]:
    return page.evaluate(f"() => [...document.querySelectorAll('a[{attr}][aria-current=page]')]"
                         f".map((a) => a.getAttribute('{attr}'))")


# ================================================================ демо: главная и инструменты
def test_demo_home(demo):
    """Главная v2 (CONTRACT «Главная v2»): 8 блоков по порядку и их ссылки; кнопка ассистента видна."""
    demo.goto("?demo=1")
    page = demo.page
    assert demo.hash() == "#/", "?demo=1 без маршрута открывает главную"
    assert page.locator("#demo-banner").is_visible(), "нет плашки демо-режима"
    assert demo.visible_panel() == ["panel-home"]
    assert _current(page, "data-nav") == [], "пункта «Главная» в меню нет"
    assert page.locator("a[data-nav=home]").count() == 0
    # 1 герой
    assert page.locator("#nav-search").is_visible() and page.locator("#home-search").is_visible()
    assert page.locator("#panel-home .hero a[href='#/tools/backtest']").is_visible(), "кнопка «Проверить портфель»"
    assert page.locator("#panel-home .hero a[href='#/portfolios/lazy']").is_visible(), "кнопка «Готовые портфели»"
    assert page.locator("#h-home .hl").count() == 1, "выделенное слово в заголовке героя"
    # 2–7 блоки по порядку (8 — подвал)
    order = page.evaluate("""() => [...document.querySelectorAll('#panel-home > .hero, #panel-home > .home-block')]
        .map((e) => e.classList.contains('hero') ? 'hero' : (e.querySelector('[id^=home-], .cta-links') || e).id || 'cta')""")
    assert order == ["hero", "home-portfolios", "home-steps", "home-corr", "home-compare", "home-popular", "home-indices", "cta"], order
    assert page.locator("footer").count() >= 1, "нет подвала"
    page.wait_for_selector("#home-portfolios a.pf-card[href^='#/portfolio/']", timeout=TIMEOUT)
    assert page.locator("#home-steps > li > a.tile").count() == 5
    assert page.locator("#panel-home .divers a[href='#/tools/frontier']").count() == 1
    assert page.locator("#panel-home .divers a[href='#/tools/backtest']").count() == 1
    assert page.locator("#home-compare #cmp-a").is_visible() and page.locator("#home-pairs button.pair").count() >= 3
    assert page.locator("#home-popular li").count() >= 10
    cta = page.evaluate("() => [...document.querySelectorAll('.cta-strip a')].map((a) => a.getAttribute('href'))")
    assert cta == ["#/portfolios/lazy", "#/screener/stocks", "#/docs"], cta
    assert page.locator("#tool-nav").is_hidden()
    assert page.locator(".ai-fab").is_visible(), "кнопка «Спросить ИИ» видна и на главной"
    assert page.locator("#engine-banner .notice-error").count() == 0, page.locator("#engine-banner").inner_text()
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    _assert_no_bad_text(page, "#panel-home")
    demo.check_clean()


def test_demo_home_strategies_strip(demo):
    """Лента стратегий: ≥ 10 карточек, у каждой SVG-спарклайн и бейдж 6М/YTD."""
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_selector("#home-portfolios a.pf-card[href^='#/portfolio/']", timeout=TIMEOUT)
    cards = page.evaluate("""() => [...document.querySelectorAll('#home-portfolios a.pf-card')].map((c) => ({
        spark: !!c.querySelector('svg.spark'), badge: (c.querySelector('.badge') || {}).textContent || ''}))""")
    assert len(cards) >= 10, len(cards)
    assert all(c["spark"] for c in cards), "у карточки нет спарклайна"
    assert all(c["badge"].strip() in ("6М", "YTD") for c in cards), [c["badge"] for c in cards]
    demo.check_clean()


def test_demo_home_bento(demo):
    """Бенто 01–05: каждая плашка — ссылка в свой раздел; в 02 строки с тикерами; в 05 кольцевая диаграмма."""
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_selector("#tile-5 svg.donut circle", timeout=TIMEOUT)
    page.wait_for_selector("#tile-2 .mt-row:not(.mt-head)", timeout=TIMEOUT)
    tiles = page.evaluate("""() => [...document.querySelectorAll('#home-steps > li > a.tile')].map((a) => [
        a.querySelector('.tile-n').textContent, a.getAttribute('href')])""")
    assert [t[0][-2:] for t in tiles] == ["01", "02", "03", "04", "05"], tiles
    # содержимое плашки определяет раздел: 01 портфели, 02 рейтинг акций, 03 фонды, 04 бэктест, 05 доли
    assert [t[1] for t in tiles] == ["#/portfolios/lazy", "#/screener/stocks", "#/screener/funds",
                                     "#/tools/backtest", "#/tools/frontier"], tiles
    keys = page.evaluate("() => [...document.querySelectorAll('#tile-2 .mt-row:not(.mt-head) .mt-key')].map((e) => e.textContent.trim())")
    # тест подменяет symbol_stats.json синтетикой iss_fake (в ней 3 акции): топ — min(5, число акций)
    n_shares = sum(1 for v in iss_fake.symbol_stats()["items"].values() if v.get("class") == "share")
    assert len(keys) == min(5, n_shares) and all(re.fullmatch(r"[A-Z0-9]{1,6}", k) for k in keys), keys
    assert page.locator("#tile-5 svg.donut circle").count() >= 2
    assert page.locator("#tile-5 .lg-row").count() == page.locator("#tile-5 svg.donut circle").count()
    demo.check_clean()


def test_demo_home_correlation_heatmap(demo):
    """Тепловая карта N×N: значения в [-1;1], диагональ = 1; карточки содержат числа из карты."""
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_selector("#home-corr table.heatmap tbody tr", timeout=TIMEOUT)
    m = page.evaluate("""() => [...document.querySelectorAll('#home-corr table.heatmap tbody tr')]
        .map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()))""")
    n = len(m)
    assert 5 <= n <= 6 and all(len(r) == n for r in m), m
    v = [[float(x.replace("−", "-").replace(",", ".")) for x in r] for r in m]
    assert all(-1 <= x <= 1 for r in v for x in r), v
    assert all(v[i][i] == 1 for i in range(n)), "диагональ не 1"
    assert all(v[i][j] == v[j][i] for i in range(n) for j in range(n)), "карта несимметрична"
    off = [m[i][j] for i in range(n) for j in range(n) if i != j]
    offv = [v[i][j] for i in range(n) for j in range(n) if i != j]
    nums = page.evaluate("() => [...document.querySelectorAll('#corr-seen .num, #corr-means .num')].map((e) => e.textContent.trim())")
    hi, lo = off[offv.index(max(offv))], off[offv.index(min(offv))]
    assert hi in nums and lo in nums, (nums, hi, lo)
    assert all(x in off for x in nums), (nums, off)
    demo.check_clean()


def test_demo_home_popular_with_logos(demo):
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_selector("#home-popular a.pop-card", timeout=TIMEOUT)
    cards = page.evaluate("""() => [...document.querySelectorAll('#home-popular a.pop-card')].map((a) => ({
        href: a.getAttribute('href'), logo: !!a.querySelector('.asset-badge'),
        key: (a.querySelector('.pop-key') || {}).textContent || '', name: (a.querySelector('.pop-name') || {}).textContent || ''}))""")
    assert len(cards) >= 10, cards
    for c in cards:
        assert c["logo"] and c["key"] and c["name"] and c["href"] == f"#/symbol/{c['key']}", c
    demo.check_clean()


def test_demo_home_compare_opens_backtest_5050(demo):
    demo.goto("?demo=1")
    page = demo.page
    page.locator("#home-compare button[type=submit]").click()
    assert "обе" in page.locator("#cmp-status").inner_text()
    page.locator("#home-pairs button.pair", has_text="SBER и GAZP").click()
    page.wait_for_function("() => location.hash === '#/tools/backtest' && !document.querySelector('#panel-backtest').hidden",
                           timeout=TIMEOUT)
    rows = page.evaluate("() => [...document.querySelectorAll('#bt-pf-table tbody tr')].map((tr) =>"
                         " [...tr.querySelectorAll('input')].map((i) => i.value))")
    assert [r[:2] for r in rows] == [["SBER", "50"], ["GAZP", "50"]], rows
    assert page.locator("#bt-pf-on").is_checked()
    demo.check_clean()


def test_header_brand_and_screener_submenu(demo):
    """Главная — через логотип .brand; «Скринеры» — кнопка подменю (aria-expanded), пункты ведут на скринеры."""
    demo.goto("?demo=1", "#/docs")
    page = demo.page
    btn = page.locator("#nav-sub-btn")
    assert btn.evaluate("(e) => e.tagName") == "BUTTON" and btn.get_attribute("aria-expanded") == "false"
    btn.click()
    assert btn.get_attribute("aria-expanded") == "true"
    page.locator("#nav-screeners a[href='#/screener/funds']").click()
    page.wait_for_function("() => location.hash === '#/screener/funds' && !document.querySelector('#panel-screener').hidden",
                           timeout=TIMEOUT)
    assert btn.get_attribute("aria-expanded") == "false"
    assert btn.get_attribute("aria-current") == "page"
    btn.click()
    page.keyboard.press("Escape")
    assert btn.get_attribute("aria-expanded") == "false"
    page.locator("header a.brand").click()
    page.wait_for_function("() => location.hash === '#/' && !document.querySelector('#panel-home').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-home"]
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()


def test_demo_tools_backtest_frontier_metrics_docs(demo):
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    assert demo.visible_panel() == ["panel-backtest"]
    assert _current(page, "data-tool") == ["backtest"]
    tbtn = page.locator("#nav-tools-btn")
    assert tbtn.evaluate("(e) => e.tagName") == "BUTTON" and tbtn.get_attribute("aria-current") == "page"
    assert tbtn.get_attribute("aria-expanded") == "false"
    assert page.locator("#tool-nav").is_visible()
    assert page.locator("#panel-backtest .crumbs a[href='#/tools']").count() == 1, "хлебные крошки"

    # Бэктест: в демо считается сразу; плюс явный запуск кнопкой
    page.wait_for_selector("#bt-out table.metrics-table", timeout=TIMEOUT)
    _rerun(page, "Посчитать", "#bt-out")
    _assert_table_and_chart(page, "#bt-out", "table.metrics-table")
    text = page.locator("#bt-out table.metrics-table").inner_text()
    assert "Коэф. Шарпа" in text and "Лежебока" in text, text[:500]

    # Граница Марковица — переход по навигации инструментов
    page.locator("#tool-nav a[data-tool=frontier]").click()
    page.wait_for_function("() => location.hash === '#/tools/frontier' && !document.querySelector('#panel-frontier').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-frontier"]
    assert _current(page, "data-tool") == ["frontier"]
    assert page.locator("#nav-tools-btn").get_attribute("aria-current") == "page"
    page.wait_for_selector("#fr-out canvas", timeout=TIMEOUT)
    _rerun(page, "Построить границу", "#fr-out")
    _assert_table_and_chart(page, "#fr-out")

    # Коэффициенты бумаги (по умолчанию MCFTR против MCFTR) — через подменю «Инструменты»
    tbtn.click()
    assert tbtn.get_attribute("aria-expanded") == "true"
    page.locator("#nav-tools a[href='#/tools/metrics']").click()
    page.wait_for_function("() => location.hash === '#/tools/metrics' && !document.querySelector('#panel-security').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-security"]
    assert _current(page, "data-tool") == ["metrics"]
    assert tbtn.get_attribute("aria-expanded") == "false"
    page.wait_for_selector("#sec-out table.metrics-table", timeout=TIMEOUT)
    _rerun(page, "Рассчитать", "#sec-out")
    _assert_table_and_chart(page, "#sec-out", "table.metrics-table")
    text = page.locator("#sec-out table.metrics-table").inner_text()
    for label in ("Коэф. Мартина", "Индекс язвы", "R² с бенчмарком", "Захват роста", "Захват падения"):
        assert label in text, f"нет строки «{label}» в таблице коэффициентов"

    # Методика — через главное меню
    page.locator("a[data-nav=docs]").first.click()
    page.wait_for_function("() => location.hash === '#/docs' && !document.querySelector('#panel-method').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-method"]
    assert _current(page, "data-nav") == ["docs"] and _current(page, "data-tool") == []
    assert tbtn.get_attribute("aria-current") is None
    assert page.locator("#tool-nav").is_hidden()
    assert page.locator("#formula-list .formula").count() >= 10
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()


@pytest.mark.parametrize("hash_, visible", [
    ("#/", ()), ("#/tools", ()), ("#/docs", ()), ("#/screener/stocks", ()),
    ("#/tools/backtest", ("#g-rf", "#g-bench", "#g-freq")),
    ("#/tools/frontier", ("#g-rf", "#g-bench", "#g-freq")),
    ("#/tools/metrics", ("#g-rf", "#g-bench", "#g-freq")),
    ("#/symbol/SBER", ("#g-rf",)),
])
def test_params_bar_by_route(demo, hash_, visible):
    """Общие параметры (ROUTES.params в router.js): инструменты — все; страница бумаги — только ставка; прочие — нет."""
    demo.goto("?demo=1", hash_)
    page = demo.page
    assert page.locator("#params-bar").is_visible() == bool(visible), f"{hash_}: панель параметров"
    for sel in ("#g-rf", "#g-bench", "#g-freq"):
        assert page.locator(sel).is_visible() == (sel in visible), f"{hash_}: поле {sel}"
    demo.check_clean()


def test_demo_docs_has_stage1_formulas(demo):
    """Инвариант 2: новые коэффициенты описаны во вкладке «Методика» веб-версии."""
    demo.goto("?demo=1", "#/docs")
    text = demo.page.locator("#panel-method").inner_text()
    for word in ("Мартин", "язвы", "R²", "capture", "Паркинсон", "Гарман", "Роджерс", "Янг", "моментум"):
        assert word.lower() in text.lower(), f"в методике нет «{word}»"
    demo.check_clean()


def test_demo_tools_catalog_and_stubs(demo):
    demo.goto("?demo=1", "#/tools")
    page = demo.page
    assert demo.visible_panel() == ["panel-tools"]
    assert page.locator("#nav-tools-btn").get_attribute("aria-current") == "page"
    hrefs = page.evaluate("() => [...document.querySelectorAll('#tools-groups a')].map((a) => a.getAttribute('href'))")
    for h in ("#/tools/backtest", "#/tools/frontier", "#/tools/metrics"):
        assert any(x and x.startswith(h) for x in hrefs), f"в каталоге нет ссылки {h}"

    for route, panel, nav in (("#/screener/stocks", "screener", "screener"), ("#/screener/funds", "screener", "screener"),
                              ("#/screener/bonds", "screener", "screener"), ("#/portfolios/lazy", "portfolios", "portfolios"),
                              ("#/portfolio/60-40", "portfolios", "portfolios")):
        page.evaluate(f"() => {{ location.hash = '{route}'; }}")
        page.wait_for_function(f"() => !document.querySelector('#panel-{panel}').hidden", timeout=TIMEOUT)
        assert demo.visible_panel() == [f"panel-{panel}"], route
        cur = page.evaluate("() => [...document.querySelectorAll('[data-nav][aria-current=page]')].map((a) => a.dataset.nav)")
        assert cur == [nav], route

    page.evaluate("() => { location.hash = '#/no/such/page'; }")
    page.wait_for_function("() => document.querySelector('#stub-title').textContent === 'Страница не найдена'", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-stub"]
    assert _current(page, "data-nav") == []
    # файла скринера облигаций в репозитории ещё нет (отдельный шаг workflow); разрешён только 404 на него
    demo.check_clean(allow=(r"404.*screener_bonds\.json",))


def test_demo_symbol_page_explains_live_only(demo):
    demo.goto("?demo=1", "#/symbol/SBER")
    assert demo.visible_panel() == ["panel-symbol"]
    text = demo.page.locator("#sym-root").inner_text()
    assert "живых данных" in text
    demo.check_clean()


@pytest.mark.parametrize("query, expected_hash, panel", [
    ("?demo=1&tab=backtest", "#/tools/backtest", "panel-backtest"),
    ("?demo=1&tab=frontier", "#/tools/frontier", "panel-frontier"),
    ("?demo=1&tab=security", "#/tools/metrics", "panel-security"),
    ("?demo=1&tab=method", "#/docs", "panel-method"),
    ("?demo=1&from=2015-01-01&cap=500000", "#/tools/backtest", "panel-backtest"),   # вкладка по умолчанию не писалась
    ("?demo=1&fa=MCFTR,RGBITR", "#/tools/frontier", "panel-frontier"),
    ("?demo=1&sec=MCFTR", "#/tools/metrics", "panel-security"),
    ("?demo=1&tab=nosuch", "#/", "panel-home"),
])
def test_legacy_links_redirect(demo, query, expected_hash, panel):
    demo.goto(query)
    assert demo.hash() == expected_hash
    assert demo.visible_panel() == [panel]
    search = demo.page.evaluate("() => location.search")
    assert "tab=" not in search, f"старый параметр tab остался в адресе: {search}"
    assert "demo=1" in search, "параметры состояния сохраняются"
    demo.check_clean()


def test_legacy_backtest_params_survive(demo):
    demo.goto("?demo=1&from=2015-01-01&till=2020-12-31")
    assert demo.hash() == "#/tools/backtest"
    assert demo.page.locator("#bt-from").input_value() == "2015-01-01"
    assert demo.page.locator("#bt-till").input_value() == "2020-12-31"
    demo.check_clean()


# ================================================================ живой режим (поддельный ISS)
def _wait_blocks(page, n_min: int):
    page.wait_for_function(
        "(n) => { const b = document.querySelectorAll('#sym-root .sym-block');"
        " return b.length >= n && ![...b].some((x) => x.hasAttribute('aria-busy')); }",
        arg=n_min, timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)


def _blocks(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('#sym-root .sym-block')].map((b) => b.id)")


def _assert_no_bad_text(page, sel="#panel-symbol"):
    text = page.locator(sel).inner_text()
    bad = BAD_TEXT.findall(text)
    assert not bad, f"в тексте страницы {bad}:\n" + "\n".join(
        line for line in text.splitlines() if BAD_TEXT.search(line))[:2000]


def _assert_blocks_ok(page, ids):
    for i in ids:
        loc = page.locator(f"#sym-{i}")
        assert loc.count() == 1, f"нет блока #sym-{i}; есть: {_blocks(page)}"
        body = loc.inner_text()
        assert "Блок недоступен" not in body and "Нет данных" not in body, f"#sym-{i}: {body[:600]}"
        assert loc.locator(".notice-error, .notice-warn").count() == 0, f"#sym-{i}: {body[:600]}"


SHARE_BLOCKS = ["key", "chart", "periods", "momentum", "monthly", "rating", "relative", "riskret", "tail", "divs", "drawdowns", "vol"]


def test_live_home_leaders_and_search(live):
    live.goto("")
    page = live.page
    assert live.hash() == "#/"
    assert page.locator("#demo-banner").is_hidden()
    # «Лидеры рейтинга» в v2 — плашка 02 бенто (топ-5 по symbol_stats)
    page.wait_for_selector("#tile-2 .mt-row:not(.mt-head)", timeout=TIMEOUT)
    assert "LKOH" in page.locator("#tile-2").inner_text(), page.locator("#tile-2").inner_text()[:500]
    # поиск в шапке: подсказки ISS, выбор стрелкой и Enter -> страница бумаги
    page.locator("#nav-search").fill("сбер")
    page.wait_for_selector(".search-list:not([hidden]) .search-opt", timeout=TIMEOUT)
    assert "SBER" in page.locator(".search-list:not([hidden])").inner_text()
    page.locator("#nav-search").press("ArrowDown")
    page.locator("#nav-search").press("Enter")
    page.wait_for_function("() => location.hash === '#/symbol/SBER'", timeout=TIMEOUT)
    # панель переключается по hashchange — это следующая задача после смены адреса
    page.wait_for_function("() => !document.querySelector('#panel-symbol').hidden", timeout=TIMEOUT)
    assert live.visible_panel() == ["panel-symbol"]
    _wait_blocks(page, len(SHARE_BLOCKS))
    assert any("securities.json" in u and "q=" in u for u in live.iss_calls)
    live.check_clean()


def test_live_symbol_share(live):
    live.goto("", "#/symbol/SBER")
    page = live.page
    _wait_blocks(page, len(SHARE_BLOCKS))
    assert live.visible_panel() == ["panel-symbol"]
    assert page.locator("#sym-title").inner_text() == "Сбербанк России ПАО ао"
    blocks = _blocks(page)
    assert blocks == [f"sym-{b}" for b in SHARE_BLOCKS], blocks
    _assert_blocks_ok(page, SHARE_BLOCKS)
    head = page.locator("#sym-root .sym-head").inner_text()
    assert "RU0009029540" in head and "ПАО Сбербанк" in head and "1997" in head, head
    assert page.locator("#sym-chart canvas").count() == 1
    assert page.locator("#sym-periods tbody tr").count() == 10
    assert page.locator("#sym-monthly tbody tr").count() >= 10
    assert "из 100" in page.locator("#sym-rating").inner_text()
    rel = page.locator("#sym-relative").inner_text()
    assert "R²" in rel and "Захват роста" in rel and "Бета" in rel
    assert "Коэф. Мартина" in page.locator("#sym-riskret").inner_text()
    divs = page.locator("#sym-divs").inner_text()
    assert "T-Invest" in divs and page.locator("#sym-divs table").count() >= 2
    assert page.locator("#sym-drawdowns tbody tr").count() >= 1
    vol = page.locator("#sym-vol").inner_text()
    for est in ("Паркинсон", "Гарман–Класс", "Роджерс–Сатчелл", "Янг–Чжан"):
        assert est in vol
    assert page.locator("#sym-fund, #sym-coupons, #sym-bond").count() == 0
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_chart_controls_and_benchmark(live):
    live.goto("", "#/symbol/SBER")
    page = live.page
    _wait_blocks(page, len(SHARE_BLOCKS))
    page.locator("#sym-chart button[data-v=ALL]").click()
    assert "p=ALL" in live.hash()
    page.locator("#sym-chart button[data-v=price]").click()
    assert "m=price" in live.hash()
    page.select_option("#sym-bench", "IMOEX")
    page.wait_for_function("() => location.hash.includes('b=IMOEX')", timeout=TIMEOUT)
    _wait_blocks(page, len(SHARE_BLOCKS))
    assert "IMOEX" in page.locator("#sym-relative .card-title").inner_text()
    _assert_blocks_ok(page, SHARE_BLOCKS)
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_fund(live):
    live.goto("", "#/symbol/LQDT")
    page = live.page
    _wait_blocks(page, 10)
    blocks = _blocks(page)
    assert "sym-fund" in blocks and "sym-divs" not in blocks and "sym-coupons" not in blocks, blocks
    _assert_blocks_ok(page, ["key", "chart", "periods", "fund", "rating", "relative", "drawdowns", "vol"])
    fund = page.locator("#sym-fund").inner_text()
    assert "0,29" in fund and "ВИМ Инвестиции" in fund, fund
    assert page.locator("#sym-fund a[href^='https://rusetfs.com']").count() == 1, "ссылка на источник RusETFs обязательна"
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_bond(live):
    live.goto("", "#/symbol/SU26238RMFS4")
    page = live.page
    _wait_blocks(page, 10)
    blocks = _blocks(page)
    assert "sym-coupons" in blocks and "sym-bond" in blocks and "sym-divs" not in blocks, blocks
    _assert_blocks_ok(page, ["key", "chart", "coupons", "bond", "drawdowns", "vol"])
    bond = page.locator("#sym-bond").inner_text()
    assert "14,25" in bond and "7,10" in bond and "182" in bond, bond
    assert page.locator("#sym-coupons table tbody tr").count() >= 1
    assert "RGBITR" in page.locator("#sym-relative .card-title").inner_text(), "бенчмарк ОФЗ по умолчанию — RGBITR"
    assert any("bondization" in u for u in live.iss_calls)
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_index(live):
    live.goto("", "#/symbol/IMOEX")
    page = live.page
    _wait_blocks(page, 8)
    _assert_blocks_ok(page, ["key", "chart", "periods", "drawdowns", "vol"])
    assert "MCFTR" in page.locator("#sym-relative .card-title").inner_text(), "IMOEX не сравнивается сам с собой"
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_unknown_ticker(live):
    live.goto("", "#/symbol/NOPE")
    page = live.page
    page.wait_for_function(
        "() => { const b = document.querySelectorAll('#sym-root .sym-block');"
        " return b.length > 0 && ![...b].some((x) => x.hasAttribute('aria-busy')); }", timeout=TIMEOUT)
    text = page.locator("#sym-root").inner_text()
    assert "не найден" in text, text[:1000]
    assert page.locator("#sym-root .sym-head .notice-warn, #sym-root .sym-head .notice").count() >= 1
    _assert_no_bad_text(page)
    live.check_clean()


def test_live_symbol_invalid_ticker(live):
    live.goto("", "#/symbol/%3Cb%3E")
    text = live.page.locator("#sym-root").inner_text()
    assert "не похоже на тикер" in text, text
    assert live.iss_calls == [] or all("%3C" not in u and "<" not in u for u in live.iss_calls)
    live.check_clean()


def test_live_symbol_without_symbol_stats(browser, server):
    s = Session(browser, server, live=True, stats=False)
    try:
        s.goto("", "#/symbol/SBER")
        _wait_blocks(s.page, len(SHARE_BLOCKS))
        assert "ночного пересчёта" in s.page.locator("#sym-rating").inner_text()
        _assert_no_bad_text(s.page)
        # 404 на отсутствующий файл браузер пишет в консоль сам — других ошибок быть не должно
        s.check_clean(allow=(r"Failed to load resource.*404", r"symbol_stats\.json"))
    finally:
        s.close()


# ================================================================ мобильная ширина
_WIDE_JS = ("() => [...document.querySelectorAll('body *')].filter((e) => { const r = e.getBoundingClientRect();"
            " if (!(r.width > 0 && r.right > document.documentElement.clientWidth + 1)) return false;"
            " for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {"
            "   const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; }"
            " return true; })"
            ".slice(0, 5).map((e) => e.tagName + '#' + e.id + '.' + e.className)")


@pytest.mark.parametrize("live_mode, hash_, ready", [
    (False, "#/", "#home-corr table.heatmap"), (False, "#/tools", None), (False, "#/tools/backtest", "#bt-out table"),
    (False, "#/docs", None), (False, "#/screener/stocks", "#screener-root table"),
    (False, "#/portfolios/lazy", "#portfolios-root table"), (False, "#/portfolio/lazy-plus", "#portfolios-root canvas"),
    (True, "#/", "#tile-2 .mt-row"), (True, "#/screener/stocks", "#screener-root table"),
    (True, "#/symbol/SBER", None), (True, "#/symbol/SU26238RMFS4", None),
])
def test_mobile_375_no_horizontal_scroll(browser, server, live_mode, hash_, ready):
    s = Session(browser, server, live=live_mode, width=375)
    try:
        s.goto("" if live_mode else "?demo=1", hash_)
        if hash_.startswith("#/symbol"):
            _wait_blocks(s.page, 8)
        if ready:
            s.page.wait_for_selector(ready, timeout=TIMEOUT)
        sw = s.page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw[0] <= sw[1], f"{hash_}: горизонтальная прокрутка {sw[0]} > {sw[1]}"
        wide = s.page.evaluate(_WIDE_JS)
        assert not wide, f"{hash_}: элементы шире экрана: {wide}"
        assert s.page.locator("#nav-toggle").is_visible(), "на мобильной ширине меню — за кнопкой"
        s.check_clean()
    finally:
        s.close()


def test_no_simple_mode_leftovers(demo):
    """Режим «Просто / Подробно» удалён: нет кнопки, ia-mode, data-mode и скрытых строк таблицы."""
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    assert page.locator("#mode-toggle").count() == 0
    assert page.evaluate("() => document.documentElement.dataset.mode") is None
    assert page.evaluate("() => localStorage.getItem('ia-mode')") is None
    assert page.locator(".adv").count() == 0
    assert page.locator("#params-bar").is_visible() and page.locator("#bt-band").is_visible()
    _rerun(page, "Посчитать", "#bt-out")
    assert page.locator("#bt-out table.metrics-table tbody tr:not(:visible)").count() == 0
    table = page.locator("#bt-out table.metrics-table").inner_text()
    assert "Коэф. Шарпа" in table and "Макс. просадка" in table
    summary = page.locator("#bt-out .plain-summary").inner_text()
    assert "стало бы" in summary and "не гарантирует" in summary, summary[:500]
    # подсказка «?» открывается по нажатию (телефон — без наведения)
    page.locator("#bt-out .metrics-table .gl-btn").first.click()
    assert page.locator(".gl-pop:popover-open").count() == 1
    page.keyboard.press("Escape")
    demo.check_clean()


# ================================================================ сводка «Если коротко» и подписи
_PCT = re.compile(r"(−?\d[\d ]*(?:,\d+)?) %")
_NBSP_JS = ".replace(/\\u00a0/g, ' ')"  # в тексте интерфейса — неразрывные пробелы; сравниваем с обычными


def _pct(s: str) -> float:
    """«−12,3 %» (неразрывные пробелы, типографский минус) → −12.3."""
    m = _PCT.search(s)
    assert m, f"нет процента в «{s}»"
    return float(m.group(1).replace(" ", "").replace(",", ".").replace("−", "-"))


def _summary(page) -> dict[str, str]:
    """Абзацы `.plain-summary` (без дисклеймера): имя портфеля (первый <b>) → полный текст."""
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#bt-out .plain-summary p:not(.hint)')]
        .map((p) => [p.querySelector('b').textContent, p.textContent""" + _NBSP_JS + """]))""")


def _metrics(page, out="#bt-out") -> dict[str, dict[str, str]]:
    """Таблица показателей по textContent (и скрытые в «Просто» строки): подпись строки → {столбец: значение}."""
    return page.evaluate(f"""() => {{
        const t = document.querySelector('{out} table.metrics-table');
        const cols = [...t.querySelectorAll('thead th')].slice(1).map((th) => th.textContent.trim());
        return Object.fromEntries([...t.querySelectorAll('tbody tr')].map((tr) => [
            tr.querySelector('th').getAttribute('aria-label') || tr.querySelector('th').textContent.trim(),
            Object.fromEntries([...tr.querySelectorAll('td')].map((td, i) => [cols[i], td.textContent.trim(){_NBSP_JS}]))]));
    }}""")


CONTRIB = " (по стоимости, включая взносы)"


def test_summary_matches_metrics_table(demo):
    """«в среднем X % в год» и «ниже максимума на Y %» в сводке — те же CAGR и макс. просадка, что в таблице."""
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    _rerun(page, "Посчитать", "#bt-out")
    summ, table = _summary(page), _metrics(page)
    assert len(summ) >= 3, summ
    for name, text in summ.items():
        m = re.search(r"в среднем (\S+ %) в год", text)
        assert m, f"{name}: нет «в среднем … в год»: {text}"
        cagr = _pct(table["CAGR (среднегод.)"][name])
        assert _pct(m.group(1)) == cagr, f"{name}: сводка {m.group(1)} ≠ CAGR в таблице {cagr}"
        m = re.search(r"максимума на (\S+ %)", text)
        assert m, f"{name}: нет просадки: {text}"
        dd = abs(_pct(table["Макс. просадка"][name]))  # в таблице 1 знак, в сводке — целые
        assert abs(_pct(m.group(1)) - dd) <= 0.5 + 1e-9, f"{name}: сводка {m.group(1)} ≠ просадка {dd}"
    demo.check_clean()


def _xirr_reference(cap: float, contrib: float) -> dict[str, float]:
    """XIRR по потокам Python-бэктеста (core) на той же месячной фикстуре, что и демо: −invested[0], −взносы, +итог."""
    from core.analytics.backtest import BacktestConfig, run_backtest, xirr
    from core.analytics.strategies import STRATEGIES
    from tests.web.fixture_loader import load_fixture

    px = load_fixture().loc["2011-01-31":"2025-12-31"]
    out = {}
    for name, s in STRATEGIES.items():
        r = run_backtest(px, BacktestConfig(weights=s["weights"], initial=cap, rebalance="A",
                                            contribution=contrib, contribution_freq="M"))
        inv = r.invested
        # первая дата — конец месяца, поэтому пополнение уже в ней: invested[0] = cap + contrib
        flows = [(inv.index[0], -float(inv.iloc[0]))] + [(d, -float(v)) for d, v in inv.diff().iloc[1:].items() if v > 0]
        flows.append((r.equity.index[-1], float(r.equity.iloc[-1])))
        out[name] = xirr(flows)
    return out


def test_summary_with_contributions_shows_xirr(demo):
    """С пополнениями показывается денежно-взвешенная доходность (XIRR потоков), а не CAGR ряда стоимости;
    строки CAGR и накопленной доходности видны и подписаны «(по стоимости, включая взносы)»."""
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    page.wait_for_selector("#bt-out table.metrics-table", timeout=TIMEOUT)
    page.fill("#bt-contrib", "50000")
    _rerun(page, "Посчитать", "#bt-out")
    summ, table = _summary(page), _metrics(page)
    for label in ("CAGR (среднегод.)", "Накопленная доходность"):
        assert label not in table, f"«{label}» без пометки о взносах"
        row = page.locator("#bt-out table.metrics-table tbody tr", has=page.locator(f"th[aria-label='{label}{CONTRIB}']"))
        assert row.count() == 1 and row.is_visible(), f"«{label}{CONTRIB}» не видна"
        assert f"{label}{CONTRIB}" in row.locator("th").inner_text()
    kpis = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#bt-out .kpi:not(.bench)')]"
                         ".map((k) => [k.querySelector('.kpi-name').textContent, k.querySelector('.kpi-sub').textContent" + _NBSP_JS + "]))")
    ref = _xirr_reference(1_000_000, 50_000)
    assert summ and set(summ) <= set(ref), (summ.keys(), ref.keys())
    for name, text in summ.items():
        assert "с учётом пополнений" in text, f"{name}: {text}"
        assert "с учётом пополнений" in kpis[name], f"{name}: карточка «{kpis[name]}»"
        assert "внесено" in kpis[name], kpis[name]
        m = re.search(r"с учётом пополнений приносили в среднем (\S+ %) в год", text)
        assert m, text
        shown = _pct(m.group(1))
        assert _pct(kpis[name].split("с учётом пополнений", 1)[1]) == shown, (kpis[name], text)
        assert abs(shown - ref[name] * 100) <= 0.05 + 1e-6, f"{name}: показано {shown} %, XIRR {ref[name] * 100:.3f} %"
        # CAGR ряда стоимости включает взносы и поэтому заметно выше
        assert shown < _pct(table["CAGR (среднегод.)" + CONTRIB][name]) - 1, (name, shown)
    demo.check_clean()


def test_summary_short_period_total_return(demo):
    """Срок < 1 года: годовой процент не показывается, только «за весь срок»; с пополнениями — «сверх внесённого»."""
    demo.goto("?demo=1&from=2024-01-01&till=2024-06-30", "#/tools/backtest")
    page = demo.page
    _rerun(page, "Посчитать", "#bt-out")
    summ = _summary(page)
    assert summ
    for name, text in summ.items():
        assert "в год" not in text, f"{name}: {text}"
        assert "за весь срок" in text, f"{name}: {text}"
    kpi = page.locator("#bt-out .kpis").inner_text()
    assert "в год" not in kpi and "за весь срок" in kpi, kpi

    demo.goto("?demo=1&contrib=50000&from=2024-01-01&till=2024-06-30", "#/tools/backtest")
    _rerun(page, "Посчитать", "#bt-out")
    summ = _summary(page)
    assert summ
    for name, text in summ.items():
        assert "за весь срок" in text and "сверх внесённого" in text, f"{name}: {text}"
        assert "в год" not in text, f"{name}: {text}"
    demo.check_clean()


def test_security_metrics_full_table(demo):
    demo.goto("?demo=1", "#/tools/metrics")
    page = demo.page
    page.wait_for_selector("#sec-out table.metrics-table", timeout=TIMEOUT)
    row = page.locator("#sec-out table.metrics-table tbody tr", has=page.locator("th[aria-label='Коэф. Шарпа']"))
    assert row.count() == 1 and row.is_visible()
    assert page.locator("#sec-out table.metrics-table tbody tr:not(:visible)").count() == 0
    demo.check_clean()


def test_strategy_labels_grouped_with_codes(demo):
    """Подписи готовых портфелей: доли по классам без повторов, коды индексов — в скобках и видны."""
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    page.wait_for_selector("#bt-strategies .check-note", timeout=TIMEOUT)
    notes = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#bt-strategies label')]"
                          ".map((l) => [l.querySelector('.check-title').innerText, l.querySelector('.check-note').innerText" + _NBSP_JS + "]))")
    allw = next(v for k, v in notes.items() if k.startswith("Всепогодный"))
    assert allw.count("Акции") == 1, allw
    assert re.search(r"Акции 37,5 % \((MCFTR, MEEUTR|MEEUTR, MCFTR)\)", allw), \
        f"MCFTR 30 % + MEEUTR 7,5 % должны сложиться в «Акции 37,5 % (MCFTR, MEEUTR)»: {allw}"
    codes = re.compile(r"\b(MCFTR|MEEUTR|RGBITR|RUGBITR\w*|GOLD_CBR|CORP_CHAIN|RUCBITR|RUONIA)\b")
    for name, text in notes.items():
        assert codes.search(text), f"{name}: нет кодов индексов: {text}"
        for part in text.split(" · "):
            assert not codes.search(part) or re.search(r"\([^)]*\)", part), f"{name}: код не в скобках: {part}"
        words = [w.split(" ")[0] for w in text.split(" · ") if w]
        assert len(words) == len(set(words)), f"{name}: класс повторяется: {text}"
    visible = page.locator("#bt-strategies .check-note .muted").first
    assert visible.is_visible(), "коды индексов скрыты"
    demo.check_clean()


# ================================================================ скринеры (web/js/page-screener.js)
def _scr_rows(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('#screener-root tbody tr th a.scr-ticker')].map((a) => a.textContent.trim())")


def _scr_count(page) -> str:
    return page.locator("#screener-root .scr-count").inner_text()


def test_screener_stocks(demo):
    demo.goto("?demo=1", "#/screener/stocks")
    page = demo.page
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-screener"]
    n0 = page.locator("#screener-root tbody tr").count()
    assert n0 >= 10
    meta = page.locator("#screener-root .scr-meta").inner_text()
    assert "FinServ" in meta and "T-Invest" in meta, meta

    # поиск сужает
    page.locator("#screener-root .scr-search input").fill("SBER")
    page.wait_for_function("() => document.querySelectorAll('#screener-root tbody tr').length >= 1"
                           " && document.querySelectorAll('#screener-root tbody tr').length < 5", timeout=TIMEOUT)
    assert any(r.startswith("SBER") for r in _scr_rows(page)), _scr_rows(page)
    assert "Найдено" in _scr_count(page)
    page.locator("#screener-root .scr-search input").fill("")

    # фильтр сектора
    sel = page.locator("#screener-root label.field", has_text="Сектор").locator("select")
    sel.select_option("Финансы")
    page.wait_for_function("() => location.hash.includes('sector=')", timeout=TIMEOUT)
    col = page.evaluate("() => [...document.querySelectorAll('#screener-root thead th')].findIndex((th) => th.textContent.startsWith('Сектор'))")
    sectors = page.evaluate(f"() => [...document.querySelectorAll('#screener-root tbody tr')].map((tr) => tr.children[{col}].textContent)")
    assert sectors and set(sectors) == {"Финансы"}, sectors

    # сортировка по клику меняет aria-sort
    th = page.locator("#screener-root thead th", has=page.locator("button.th-sort", has_text="Шарп"))
    assert th.get_attribute("aria-sort") is None
    th.locator("button.th-sort").click()
    th = page.locator("#screener-root thead th", has=page.locator("button.th-sort", has_text="Шарп"))
    assert th.get_attribute("aria-sort") == "descending"
    th.locator("button.th-sort").click()
    th = page.locator("#screener-root thead th", has=page.locator("button.th-sort", has_text="Шарп"))
    assert th.get_attribute("aria-sort") == "ascending"
    assert "sort=sharpe" in demo.hash() and "dir=asc" in demo.hash()

    # состояние в hash переживает перезагрузку
    rows_before = _scr_rows(page)
    page.reload(wait_until="load")
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    assert page.locator("#screener-root label.field", has_text="Сектор").locator("select").input_value() == "Финансы"
    assert _scr_rows(page) == rows_before
    th = page.locator("#screener-root thead th", has=page.locator("button.th-sort", has_text="Шарп"))
    assert th.get_attribute("aria-sort") == "ascending"

    # CSV
    with page.expect_download() as dl:
        page.get_by_role("button", name="Скачать CSV").click()
    assert dl.value.suggested_filename == "screener-stocks.csv"
    text = open(dl.value.path(), encoding="utf-8-sig").read()
    assert text.splitlines()[0].startswith("Тикер;") and len(text.splitlines()) == len(rows_before) + 1

    # клик по строке (не по ссылке) — страница бумаги
    first = rows_before[0]
    page.locator("#screener-root tbody tr").first.locator("td").first.click()
    page.wait_for_function(f"() => location.hash === '#/symbol/{first}'", timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()


def test_screener_funds(demo):
    demo.goto("?demo=1", "#/screener/funds")
    page = demo.page
    page.wait_for_selector("#screener-root tbody tr", timeout=TIMEOUT)
    assert page.locator("#screener-root tbody tr").count() >= 5
    assert page.locator("#screener-root .scr-tab[aria-current=page]").inner_text() == "Фонды"
    heads = page.locator("#screener-root thead").inner_text()
    assert "Комиссия" in heads and "Управляющая компания" in heads
    _assert_no_bad_text(page, "#panel-screener")
    demo.check_clean()


def test_screener_bonds_without_file(demo):
    """screener_bonds.json ещё нет (сборщик не запускался): понятное пустое состояние, без ошибок (404 разрешён)."""
    demo.goto("?demo=1", "#/screener/bonds")
    page = demo.page
    page.wait_for_selector("#screener-root .scr-body .notice, #screener-root .scr-body [role=status], #screener-root tbody tr",
                           timeout=TIMEOUT)
    page.wait_for_function("() => !document.querySelector('#screener-root [aria-busy]')", timeout=TIMEOUT)
    text = page.locator("#screener-root").inner_text()
    assert "пока нет" in text, text
    assert page.locator("#screener-root .notice-error").count() == 0
    demo.check_clean(allow=(r"Failed to load resource.*404",))


# ================================================================ портфели (web/js/page-portfolios.js)
def test_portfolios_listing_page_and_backtest(demo):
    demo.goto("?demo=1", "#/portfolios/lazy")
    page = demo.page
    page.wait_for_selector("#portfolios-root table.pf-list tbody tr", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-portfolios"]
    assert page.locator("#portfolios-root table.pf-list tbody tr").count() >= 10
    page.wait_for_selector("#portfolios-root canvas", timeout=TIMEOUT)   # «риск — доходность»
    _assert_no_bad_text(page, "#panel-portfolios")

    page.locator("#portfolios-root table.pf-list a[href='#/portfolio/lazy-plus']").click()
    page.wait_for_function("() => location.hash === '#/portfolio/lazy-plus'", timeout=TIMEOUT)
    page.wait_for_selector("#portfolios-root .pf-grid canvas", timeout=TIMEOUT)
    assert page.locator("#portfolios-root h2").first.inner_text().strip()
    assert page.locator("#portfolios-root .pf-grid table tbody tr").count() >= 2, "нет состава"
    _assert_no_bad_text(page, "#panel-portfolios")

    page.get_by_role("button", name="Открыть в бэктесте").click()
    page.wait_for_function("() => location.hash === '#/tools/backtest' && !document.querySelector('#panel-backtest').hidden",
                           timeout=TIMEOUT)
    assert page.locator("#bt-pf-on").is_checked(), "«Свой портфель» не включён"
    rows = page.evaluate("() => [...document.querySelectorAll('#bt-pf-table tbody tr')].map((tr) =>"
                         " [...tr.querySelectorAll('input')].map((i) => i.value))")
    assert len(rows) >= 2 and all(r[0] for r in rows), rows
    assert "Свой портфель" in page.locator("#bt-out").inner_text()
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()


def test_portfolio_unknown_slug(demo):
    demo.goto("?demo=1", "#/portfolio/no-such")
    page = demo.page
    page.wait_for_function("() => document.querySelector('#portfolios-root').textContent.includes('не найден')", timeout=TIMEOUT)
    demo.check_clean()


# ================================================================ логотипы компаний (web/js/logo.js)
CDN = "https://invest-brands.cdn-tinkoff.ru/"
_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _with_logo(session):
    """tinvest_shares.json: репозиторный + logo у SBER."""
    body = json.loads((ROOT / "public/data/tinvest_shares.json").read_text(encoding="utf-8"))
    for r in body["data"]:
        if r["ticker"] == "SBER":
            r["logo"], r["logo_color"] = "sber.png", "#309C0B"
    session.page.route("**/public/data/tinvest_shares.json",
                       lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False)))


def _sber_badge(page):
    return page.locator("#home-popular a[href='#/symbol/SBER'] .asset-badge")


def test_logo_initials_without_data(demo):
    demo.goto("?demo=1")
    badge = _sber_badge(demo.page)
    demo.page.wait_for_function("() => document.querySelector(\"#home-popular a[href='#/symbol/SBER'] .asset-badge\")?.dataset.initials",
                                timeout=TIMEOUT)
    assert badge.get_attribute("data-initials") == "SB"
    assert badge.locator("img").count() == 0
    demo.check_clean()


def test_logo_image_from_cdn(demo):
    _with_logo(demo)
    got = []
    demo.page.route(f"{CDN}**", lambda r: (got.append(r.request.url), r.fulfill(status=200, content_type="image/png", body=_PNG)))
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_selector("#home-popular a[href='#/symbol/SBER'] .asset-badge img", timeout=TIMEOUT)
    img = _sber_badge(page).locator("img")
    assert img.get_attribute("src") == f"{CDN}sberx160.png"
    assert img.get_attribute("alt") == ""
    page.wait_for_function("() => document.querySelector(\"#home-popular a[href='#/symbol/SBER'] .asset-badge img\").complete", timeout=TIMEOUT)
    assert got and all(u.startswith(CDN) for u in got)
    demo.check_clean()


def test_logo_falls_back_to_initials_on_error(demo):
    _with_logo(demo)
    demo.page.route(f"{CDN}**", lambda r: r.abort())
    demo.goto("?demo=1")
    page = demo.page
    page.wait_for_function("() => { const b = document.querySelector(\"#home-popular a[href='#/symbol/SBER'] .asset-badge\");"
                           " return b && b.dataset.initials === 'SB' && !b.querySelector('img') && b.style.background; }", timeout=TIMEOUT)
    assert _sber_badge(page).evaluate("(e) => getComputedStyle(e).backgroundColor") == "rgb(48, 156, 11)"
    demo.check_clean(allow=(r"ERR_FAILED|Failed to load resource",))


# ================================================================ ассистент «Спросить ИИ» (web/js/assistant.js)
OFFLINE_AI = "Ассистент работает на опубликованном сайте"
# живой бэктест на поддельном ISS: части индексов для Брауна/Далио в нём нет (404) — это не ошибка ассистента
LIVE_BT_MISSING = r"RUGBITR10Y|RUGBITR5|MEEUTR"


def _ai_requests(session):
    calls = []
    session.page.on("request", lambda r: calls.append(r) if "/api/assistant" in r.url else None)
    return calls


def test_ai_demo_panel(demo):
    calls = _ai_requests(demo)
    demo.goto("?demo=1", "#/")
    page = demo.page
    fab = page.locator(".ai-fab")
    assert fab.count() == 1 and fab.is_visible(), "на главной кнопка «Спросить ИИ» видна"

    page.evaluate("() => { location.hash = '#/tools/backtest'; }")
    page.wait_for_selector("#bt-out table.metrics-table", timeout=TIMEOUT)
    _rerun(page, "Посчитать", "#bt-out")
    assert fab.is_visible(), "после расчёта бэктеста кнопка видна"
    assert "Спросить ИИ" in fab.inner_text()

    fab.click()
    dlg = page.locator("dialog.ai-dialog")
    assert dlg.is_visible()
    assert page.evaluate("() => document.activeElement && document.activeElement.id") == "ai-q", "фокус в поле вопроса"
    screen_txt = page.locator("#ai-screen").inner_text()
    n = int(re.search(r"ИИ видит (\d+) ", screen_txt).group(1))
    word = page.evaluate(f"async () => (await import('/web/js/assistant.js')).plural({n}, 'показатель', 'показателя', 'показателей')")
    assert f"ИИ видит {n} {word} " in screen_txt, screen_txt
    assert "личные данные" in page.locator("#ai-pd").inner_text(), "подсказка про личные данные"
    assert page.locator(".ai-examples button").count() >= 1
    page.locator("#ai-q").fill("Что такое максимальная просадка?")
    page.get_by_role("button", name="Спросить", exact=True).click()
    page.wait_for_selector(".ai-answer [role=alert]", timeout=TIMEOUT)
    assert OFFLINE_AI in page.locator(".ai-answer").inner_text()
    assert not calls, "в демо запрос к ассистенту не уходит"

    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('dialog.ai-dialog').open", timeout=TIMEOUT)
    assert page.evaluate("() => document.activeElement.classList.contains('ai-fab')"), "фокус вернулся на кнопку"

    # клик по затемнению закрывает, клик внутри диалога — нет
    fab.click()
    box = dlg.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + 10)
    assert page.evaluate("() => document.querySelector('dialog.ai-dialog').open"), "клик внутри не закрывает"
    page.mouse.click(5, 5)
    page.wait_for_function("() => !document.querySelector('dialog.ai-dialog').open", timeout=TIMEOUT)

    page.evaluate("() => { location.hash = '#/docs'; }")
    page.wait_for_function("() => !document.querySelector('#panel-method').hidden", timeout=TIMEOUT)
    assert fab.is_visible(), "в методике кнопка тоже видна"
    demo.check_clean()


def _live_backtest(live):
    live.goto("", "#/tools/backtest")
    page = live.page
    page.get_by_role("button", name="Посчитать").click()
    page.wait_for_function("() => !document.body.classList.contains('is-busy') && document.querySelector('#bt-out .kpi')",
                           timeout=TIMEOUT)
    return page


def _check_live_clean(live, allow=None):
    errs = [e for e in live.errors if not re.search(LIVE_BT_MISSING, e) and not (allow and re.search(allow, e))]
    assert not errs, "\n".join(errs)
    assert not live.external, live.external
    assert all(re.search(LIVE_BT_MISSING, u) for u in live.unknown_iss), live.unknown_iss


def test_ai_live_answer_marks_unverified(live):
    page = _live_backtest(live)
    sent = []

    def fake(route):
        sent.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "answer": "Итоговая сумма у портфеля выросла, в среднем 15 % в год.",
            "facts_used": ["pf1.final"], "unverified_numbers": ["15 %"],
            "label": "Ответ ИИ (YandexGPT). Не является индивидуальной инвестиционной рекомендацией."},
            ensure_ascii=False))

    page.route("**/api/assistant", fake)
    page.locator(".ai-fab").click()
    page.locator("#ai-q").fill("Почему у портфелей разная итоговая сумма?")
    page.get_by_role("button", name="Спросить", exact=True).click()
    page.wait_for_selector(".ai-answer .ai-text", timeout=TIMEOUT)

    ans = page.locator(".ai-answer")
    assert "Ответ ИИ" in ans.locator(".ai-label").inner_text()
    mark = ans.locator("mark.ai-unverified")
    assert mark.count() == 1 and mark.inner_text().startswith("15 %")
    assert "не найдены в расчёте" in ans.inner_text()

    assert len(sent) == 1
    req = sent[0]
    assert req["question"] == "Почему у портфелей разная итоговая сумма?"
    screen = req["screen"]
    assert screen["route"] == "/tools/backtest" and screen["title"]
    ids = {f["id"] for f in screen["facts"]}
    assert {"pf1.final", "pf1.invested", "pf1.period", "pf1.max_drawdown"} <= ids, ids
    assert all(re.fullmatch(r"(pf\d+|bench)\.\w+", i) for i in ids), ids
    assert len(screen["facts"]) <= 60
    by_id = {f["id"]: f for f in screen["facts"]}
    out = page.locator("#bt-out").inner_text()
    final = by_id["pf1.final"]
    assert final["unit"] == "rub" and isinstance(final["value"], (int, float))
    assert re.fullmatch(r"\d{1,3}( \d{3})* ₽", final["text"]), final["text"]
    assert final["text"].replace(" ", " ") in out.replace(" ", " "), "текст факта — как на экране"
    dd = by_id["pf1.max_drawdown"]
    assert dd["unit"] == "pct" and re.fullmatch(r"[−-]?\d+,\d %", dd["text"]), dd["text"]
    assert all(f["text"] and f["source"] and f["label"] for f in screen["facts"])
    _check_live_clean(live)


@pytest.mark.parametrize("status, needle", [
    (429, "Слишком много вопросов"), (502, "недоступен"), (400, "Вопрос не принят: Введите вопрос."),
    (502, "Лимит запросов YandexGPT, попробуйте позже."),   # так отвечает функция на 429 от YandexGPT
])
def test_ai_live_errors(live, status, needle):
    page = _live_backtest(live)
    page.route("**/api/assistant", lambda r: r.fulfill(status=status, content_type="application/json",
                                                         body=json.dumps({"error": needle if "лимит" in needle.lower() else "Введите вопрос."},
                                                                         ensure_ascii=False)))
    page.locator(".ai-fab").click()
    page.locator("#ai-q").fill("Что такое просадка?")
    page.get_by_role("button", name="Спросить", exact=True).click()
    page.wait_for_selector(".ai-answer [role=alert]", timeout=TIMEOUT)
    assert needle in page.locator(".ai-answer").inner_text()
    assert page.get_by_role("button", name="Спросить", exact=True).is_enabled()
    _check_live_clean(live, allow=rf"status of {status} .*/api/assistant")  # строка браузера о коде ответа


def test_ai_plural(demo):
    """Склонение «1 показатель / 3 показателя / 30 показателей» (web/js/assistant.js: plural)."""
    demo.goto("?demo=1", "#/")
    got = demo.page.evaluate("async () => { const m = await import('/web/js/assistant.js');"
                             " return [1, 2, 3, 4, 5, 11, 12, 14, 21, 22, 25, 30, 101, 111, 112]"
                             ".map((n) => m.plural(n, 'показатель', 'показателя', 'показателей')); }")
    one, few, many = "показатель", "показателя", "показателей"
    assert got == [one, few, few, few, many, many, many, many, one, few, many, many, one, many, many]
    demo.check_clean()


# ================================================================ v2: страница портфеля, шрифт, ассистент
def test_portfolio_page_metrics_drawdowns_charts(demo):
    """Таблица коэффициентов — числа (не [object …]); 7 KPI просадок; графики без заливки."""
    demo.goto("?demo=1", "#/portfolio/lazy-plus")
    page = demo.page
    page.wait_for_selector("#portfolios-root table.metrics-table tbody tr", timeout=TIMEOUT)
    page.wait_for_selector("#portfolios-root .pf-grid canvas", timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    rows = page.evaluate("""() => [...document.querySelectorAll('#portfolios-root table.metrics-table tbody tr')]
        .map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()))""")
    flat = " ".join(" ".join(r) for r in rows)
    assert "[object" not in flat, flat[:300]
    with_num = [r for r in rows if any(re.search(r"\d", c) for c in r)]
    assert len(with_num) >= 10, rows
    assert page.locator("#portfolios-root .dd-kpis .kpi").count() == 7
    # графики строятся в requestAnimationFrame
    page.wait_for_function("() => [...document.querySelectorAll('#portfolios-root canvas')].some((c) => window.Chart.getChart(c))",
                           timeout=TIMEOUT)
    fills = page.evaluate("""() => [...document.querySelectorAll('#portfolios-root canvas')].map((c) => window.Chart.getChart(c))
        .filter((ch) => ch && ch.config.type === 'line').flatMap((ch) => ch.data.datasets.map((d) => d.fill))""")
    assert fills and all(f in (False, None) for f in fills), fills
    _assert_no_bad_text(page, "#panel-portfolios")
    demo.check_clean()


def _font(page, sel):
    page.wait_for_selector(sel, timeout=TIMEOUT)
    return page.locator(sel).first.evaluate("(e) => getComputedStyle(e).fontFamily")


def test_tickers_font_montserrat(demo):
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    f = _font(page, "#bt-out table.trades td.mono")
    assert f.strip('"\' ').startswith("Montserrat"), f
    page.evaluate("() => { location.hash = '#/screener/stocks'; }")
    f = _font(page, "#screener-root .scr-ticker")
    assert f.strip('"\' ').startswith("Montserrat"), f
    demo.check_clean()


@pytest.mark.parametrize("hash_", ["#/", "#/screener/stocks", "#/portfolios/lazy", "#/docs"])
def test_ai_fab_visible_everywhere(demo, hash_):
    demo.goto("?demo=1", hash_)
    fab = demo.page.locator(".ai-fab")
    assert fab.is_visible() and "Спросить ИИ" in fab.inner_text(), hash_
    demo.page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()
