"""
Смоук-тесты веб-версии (web/): статический сервер из корня репозитория + Playwright (chromium).

Демо-режим (?demo=1): все запросы не к локальному серверу блокируются и считаются ошибкой.
Живой режим (страница бумаги, поиск, главная): запросы к iss.moex.com перехватываются page.route и
получают синтетические ответы tests/iss_fake.py; public/data — локальные файлы репозитория
(symbol_stats.json подставляется синтетический, кроме теста «файла нет»). Сеть не нужна.

Маршруты этапа 1 (web/CONTRACT.md): #/, #/tools, #/tools/backtest|frontier|metrics, #/docs, #/symbol/{TICKER},
#/screener/…; раздел портфелей #/portfolios (?g=lazy|theme), #/portfolios/{id}, старый #/portfolios/lazy -> список
с фильтром «Ленивые» (public/data/portfolios.json — файл сборщика из репозитория, либо подмена через overrides);
старые ссылки ?tab=… перенаправляются.

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

    def __init__(self, browser, server, *, live: bool, stats: bool = True, width: int | None = None,
                 overrides: dict | None = None):
        """overrides: {окончание пути: (HTTP-статус, тело)} — подмена локальных файлов (например, portfolios.json)."""
        self.server = server
        overrides = overrides or {}
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
                path = urlparse(url).path
                hit = next((k for k in overrides if path.endswith(k)), None)
                if hit is not None:
                    status, body = overrides[hit]
                    r.fulfill(status=status, content_type="application/json" if status == 200 else "text/plain",
                              body=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
                elif path.endswith("/public/data/symbol_stats.json"):
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
    demo.goto("?demo=1")
    page = demo.page
    assert demo.hash() == "#/", "?demo=1 без маршрута открывает главную"
    assert page.locator("#demo-banner").is_visible(), "нет плашки демо-режима"
    assert demo.visible_panel() == ["panel-home"]
    assert _current(page, "data-nav") == ["home"]
    assert page.locator("#nav-search").is_visible()
    assert page.locator("#home-popular li").count() >= 10
    assert page.locator("#home-sections a").count() >= 4
    assert page.locator("#tool-nav").is_hidden()
    assert page.locator("#engine-banner .notice-error").count() == 0, page.locator("#engine-banner").inner_text()
    demo.check_clean()


def test_demo_tools_backtest_frontier_metrics_docs(demo):
    demo.goto("?demo=1", "#/tools/backtest")
    page = demo.page
    assert demo.visible_panel() == ["panel-backtest"]
    assert _current(page, "data-nav") == ["tools"] and _current(page, "data-tool") == ["backtest"]
    assert page.locator("#tool-nav").is_visible()

    # Бэктест: в демо считается сразу; плюс явный запуск кнопкой
    page.wait_for_selector("#bt-out table.metrics-table", timeout=TIMEOUT)
    _rerun(page, "Запустить бэктест", "#bt-out")
    _assert_table_and_chart(page, "#bt-out", "table.metrics-table")
    text = page.locator("#bt-out table.metrics-table").inner_text()
    assert "Коэф. Шарпа" in text and "Лежебока" in text, text[:500]

    # Граница Марковица — переход по навигации инструментов
    page.locator("#tool-nav a[data-tool=frontier]").click()
    page.wait_for_function("() => location.hash === '#/tools/frontier' && !document.querySelector('#panel-frontier').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-frontier"]
    assert _current(page, "data-tool") == ["frontier"] and _current(page, "data-nav") == ["tools"]
    page.wait_for_selector("#fr-out canvas", timeout=TIMEOUT)
    _rerun(page, "Построить границу", "#fr-out")
    _assert_table_and_chart(page, "#fr-out")

    # Коэффициенты бумаги (по умолчанию MCFTR против MCFTR)
    page.locator("#tool-nav a[data-tool=metrics]").click()
    page.wait_for_function("() => location.hash === '#/tools/metrics' && !document.querySelector('#panel-security').hidden", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-security"]
    assert _current(page, "data-tool") == ["metrics"]
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
    assert _current(page, "data-nav") == ["tools"]
    hrefs = page.evaluate("() => [...document.querySelectorAll('#tools-groups a')].map((a) => a.getAttribute('href'))")
    for h in ("#/tools/backtest", "#/tools/frontier", "#/tools/metrics"):
        assert any(x and x.startswith(h) for x in hrefs), f"в каталоге нет ссылки {h}"

    for route, title, nav in (("#/screener/stocks", "Скринер акций", "screener"), ("#/screener/funds", "Скринер фондов", "screener"),
                              ("#/screener/bonds", "Скринер облигаций", "screener")):
        page.evaluate(f"() => {{ location.hash = '{route}'; }}")
        page.wait_for_function(f"() => document.querySelector('#stub-title').textContent === '{title}' && "
                               f"!document.querySelector('#panel-stub').hidden", timeout=TIMEOUT)
        assert demo.visible_panel() == ["panel-stub"], route
        assert _current(page, "data-nav") == [nav], route

    # #/portfolios/lazy больше не заглушка: список портфелей с фильтром «Ленивые», адрес заменён
    page.evaluate("() => { location.hash = '#/portfolios/lazy'; }")
    page.wait_for_function("() => location.hash === '#/portfolios?g=lazy' && !document.querySelector('#panel-portfolios').hidden",
                           timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-portfolios"]
    assert _current(page, "data-nav") == ["portfolios"]

    page.evaluate("() => { location.hash = '#/no/such/page'; }")
    page.wait_for_function("() => document.querySelector('#stub-title').textContent === 'Страница не найдена'", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-stub"]
    assert _current(page, "data-nav") == []
    demo.check_clean()


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


# ================================================================ портфели и стратегии (#/portfolios)
PF_FILE = ROOT / "public" / "data" / "portfolios.json"


@functools.lru_cache(maxsize=1)
def _pf_data() -> dict:
    """portfolios.json сборщика из репозитория (тот же файл отдаёт локальный сервер)."""
    if not PF_FILE.exists():
        pytest.skip("нет public/data/portfolios.json")
    return json.loads(PF_FILE.read_text(encoding="utf-8"))


def _pf_ids(group: str | None = None) -> set[str]:
    items = _pf_data()["items"]
    return {k for k, v in items.items() if group is None or (v.get("row") or {}).get("group") == group}


LAZY = "Ленивые портфели"
PF_ROWS = "#pf-root table.pf-list tbody tr"


def _wait_pf_list(page):
    page.wait_for_selector(PF_ROWS, timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)


def _row_ids(page) -> list[str]:
    return page.evaluate(f"() => [...document.querySelectorAll('{PF_ROWS}')].map((r) => r.dataset.id)")


def _pressed(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('#pf-root .chip[aria-pressed=true]')].map((b) => b.textContent)")


def test_demo_home_dark_theme_and_portfolios_light(demo):
    """Главная — тёмная тема (body.theme-dark), рабочие страницы — светлые, и при переходе класс снимается."""
    demo.goto("?demo=1")
    page = demo.page
    is_dark = "() => document.body.classList.contains('theme-dark')"
    assert page.evaluate(is_dark), "на главной нет body.theme-dark"
    page.wait_for_selector("#home-pf a[href*='#/portfolios/']", timeout=TIMEOUT)
    for route, panel in (("#/portfolios", "panel-portfolios"), ("#/tools", "panel-tools"), ("#/docs", "panel-method"),
                         ("#/portfolios/lazy-plus", "panel-portfolio")):
        page.evaluate(f"() => {{ location.hash = '{route}'; }}")
        page.wait_for_function(f"() => !document.querySelector('#{panel}').hidden", timeout=TIMEOUT)
        assert not page.evaluate(is_dark), f"{route}: тёмная тема осталась на рабочей странице"
    page.evaluate("() => { location.hash = '#/'; }")
    page.wait_for_function(is_dark, timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    demo.check_clean()


def test_demo_portfolios_list(demo):
    demo.goto("?demo=1", "#/portfolios")
    page = demo.page
    _wait_pf_list(page)
    assert demo.visible_panel() == ["panel-portfolios"]
    assert _current(page, "data-nav") == ["portfolios"]
    ids = _row_ids(page)
    assert len(ids) == len(_pf_data()["items"]) and set(ids) == _pf_ids(), ids
    # порядок — по рейтингу (portfolioList): рейтинги в таблице не возрастают, без рейтинга — в конце
    ranks = [(_pf_data()["items"][i]["row"].get("rank")) for i in ids]
    nums = [r for r in ranks if isinstance(r, (int, float)) and r == r]
    assert ranks[:len(nums)] == nums and nums == sorted(nums, reverse=True), ranks
    assert _pressed(page) and _pressed(page)[0].startswith("Все"), _pressed(page)
    assert page.locator("#pf-root .notice-error").count() == 0
    # диаграмма риск–доходность: по точке на каждый портфель с 10-летней историей
    pts = [p for p in _pf_data().get("risk_return", {}).get("10Y", [])
           if isinstance(p.get("ret"), (int, float)) and isinstance(p.get("volatility"), (int, float))]
    assert page.locator("#pf-root svg circle.pt").count() == len(pts)
    _assert_no_bad_text(page, "#panel-portfolios")
    assert page.evaluate("() => document.title").endswith("— FinServ")
    demo.check_clean()


def test_demo_portfolios_group_filter(demo):
    demo.goto("?demo=1", "#/portfolios?g=lazy")
    page = demo.page
    _wait_pf_list(page)
    lazy = _pf_ids(LAZY)
    assert lazy, "в данных нет ленивых портфелей"
    assert set(_row_ids(page)) == lazy and len(_row_ids(page)) == len(lazy)
    assert len(_pressed(page)) == 1 and _pressed(page)[0].startswith(LAZY), _pressed(page)
    # «Все» — снять фильтр: в адресе нет g, строк — все
    page.locator("#pf-root .chip", has_text="Все").click()
    page.wait_for_function(f"() => document.querySelectorAll('{PF_ROWS}').length === {len(_pf_ids())}", timeout=TIMEOUT)
    assert "g=" not in demo.hash()
    # группа «Тематические» через пилюлю -> ?g=theme
    theme = [g for g in _pf_data()["groups"] if g != LAZY]
    if theme:
        page.locator("#pf-root .chip", has_text=theme[0]).click()
        page.wait_for_function(f"() => document.querySelectorAll('{PF_ROWS}').length === {len(_pf_ids(theme[0]))}",
                               timeout=TIMEOUT)
        assert set(_row_ids(page)) == _pf_ids(theme[0])
    demo.check_clean()


def test_demo_portfolios_legacy_lazy_route(demo):
    demo.goto("?demo=1", "#/portfolios/lazy")
    page = demo.page
    _wait_pf_list(page)
    assert demo.hash() == "#/portfolios?g=lazy", "старый #/portfolios/lazy -> список с фильтром «Ленивые»"
    assert demo.visible_panel() == ["panel-portfolios"]
    assert set(_row_ids(page)) == _pf_ids(LAZY)
    assert "demo=1" in page.evaluate("() => location.search")
    demo.check_clean()


def test_demo_portfolios_unknown_group_shows_all(demo):
    demo.goto("?demo=1", "#/portfolios?g=nosuch")
    _wait_pf_list(demo.page)
    assert set(_row_ids(demo.page)) == _pf_ids()
    demo.check_clean()


def test_demo_portfolios_row_click_opens_portfolio(demo):
    demo.goto("?demo=1", "#/portfolios")
    page = demo.page
    _wait_pf_list(page)
    first = _row_ids(page)[0]
    # клик по ячейке (не по ссылке): router.go — переход в том же такте
    page.locator(f"{PF_ROWS}[data-id='{first}'] td").nth(1).click()
    page.wait_for_function(f"() => location.hash === '#/portfolios/{first}' && !document.querySelector('#panel-portfolio').hidden",
                           timeout=TIMEOUT)
    page.wait_for_selector("#pf-repl", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-portfolio"]
    assert _current(page, "data-nav") == ["portfolios"]
    name = _pf_data()["items"][first]["report"]["name"]
    assert page.evaluate("() => document.querySelector('#pf-title').textContent").strip() == name
    # «Назад» — снова список
    page.go_back()
    page.wait_for_function("() => location.hash === '#/portfolios' && !document.querySelector('#panel-portfolios').hidden",
                           timeout=TIMEOUT)
    demo.check_clean()


def test_demo_portfolio_page_lazy_plus(demo):
    if "lazy-plus" not in _pf_ids():
        pytest.skip("в portfolios.json нет lazy-plus")
    demo.goto("?demo=1", "#/portfolios/lazy-plus")
    page = demo.page
    page.wait_for_selector("#pf-repl", timeout=TIMEOUT)
    page.wait_for_function(IDLE, timeout=TIMEOUT)
    rep = _pf_data()["items"]["lazy-plus"]["report"]
    assert demo.visible_panel() == ["panel-portfolio"]
    assert page.evaluate("() => document.querySelector('#pf-title').textContent").strip() == rep["name"]
    assert page.evaluate("() => document.title") == f"{rep['name']} — FinServ"
    crumbs = page.locator("#pf-crumbs").inner_text()
    assert "Портфели" in crumbs and rep["name"] in crumbs, crumbs
    # заголовки карточек — text-transform: uppercase, поэтому textContent, а не inner_text
    assert page.evaluate("() => document.querySelector('#pf-repl').textContent") == "Как повторить"
    assert page.locator("#pf-page .repl tbody tr").count() == len(rep.get("replicate") or []) > 0
    assert page.locator("#pf-page svg path.ln.pf").count() == 1, "нет линии роста капитала"
    assert page.locator("#pf-page table.metrics-table tbody tr").count() >= 10
    assert page.locator("#pf-page table.periods-table tbody tr").count() >= 1
    assert page.locator("#pf-page a[href^='https://rusetfs.com']").count() >= 1, "ссылка на RusETFs обязательна"
    assert page.locator("#pf-page .notice-error").count() == 0
    _assert_no_bad_text(page, "#panel-portfolio")
    demo.check_clean()


@pytest.mark.parametrize("pid", ["nope", "toString", "constructor", "__proto__"])
def test_demo_portfolio_not_found(demo, pid):
    demo.goto("?demo=1", f"#/portfolios/{pid}")
    page = demo.page
    page.wait_for_function("() => document.querySelector('#pf-title').textContent === 'Портфель не найден'", timeout=TIMEOUT)
    assert demo.visible_panel() == ["panel-portfolio"]
    assert page.locator("#pf-page a[href*='#/portfolios']").count() >= 1, "нет ссылки на список"
    assert page.evaluate("() => document.title") == "Портфель не найден — FinServ"
    demo.check_clean()


def test_demo_portfolios_stale_marked(browser, server):
    """Устаревшие данные (stale в portfolios.json): пометка в строке списка и предупреждение на странице."""
    data = json.loads(PF_FILE.read_text(encoding="utf-8")) if PF_FILE.exists() else None
    if not data or "lazy-plus" not in data["items"]:
        pytest.skip("нет lazy-plus в portfolios.json")
    data["stale"] = {"lazy-plus": {"stale_date": "2026-11-17", "reason": "тест"}}
    s = Session(browser, server, live=False, overrides={"/public/data/portfolios.json": (200, data)})
    try:
        s.goto("?demo=1", "#/portfolios")
        _wait_pf_list(s.page)
        assert s.page.locator(f"{PF_ROWS}[data-id='lazy-plus'] small.stale").count() == 1
        assert s.page.locator(f"{PF_ROWS} small.stale").count() == 1
        s.page.evaluate("() => { location.hash = '#/portfolios/lazy-plus'; }")
        s.page.wait_for_selector("#pf-repl", timeout=TIMEOUT)
        assert "показан предыдущий расчёт" in s.page.locator("#pf-page").inner_text()
        s.check_clean()
    finally:
        s.close()


def test_demo_portfolios_file_missing(browser, server):
    """Нет portfolios.json (404): понятная ошибка на списке и на странице портфеля, главная не ломается."""
    s = Session(browser, server, live=False, overrides={"/public/data/portfolios.json": (404, "not found")})
    try:
        s.goto("?demo=1", "#/portfolios")
        s.page.wait_for_selector("#pf-root .notice-error", timeout=TIMEOUT)
        assert "Не удалось загрузить данные портфелей" in s.page.locator("#pf-root").inner_text()
        s.page.evaluate("() => { location.hash = '#/portfolios/lazy-plus'; }")
        s.page.wait_for_selector("#pf-page .notice-error", timeout=TIMEOUT)
        s.page.evaluate("() => { location.hash = '#/'; }")
        s.page.wait_for_function("() => !document.querySelector('#panel-home').hidden", timeout=TIMEOUT)
        s.page.wait_for_function("() => !/Загрузка портфелей/.test(document.querySelector('#home-pf-sec').innerText)",
                                 timeout=TIMEOUT)
        assert s.page.locator("#home-sections a").count() >= 4
        s.check_clean(allow=(r"Failed to load resource.*404", r"portfolios\.json"))
    finally:
        s.close()


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
    page.wait_for_selector("#home-leaders table", timeout=TIMEOUT)
    leaders = page.locator("#home-leaders").inner_text()
    assert "LKOH" in leaders and "LQDT" in leaders, leaders[:500]
    # поиск в шапке: подсказки ISS, выбор стрелкой и Enter -> страница бумаги
    page.locator("#nav-search").fill("сбер")
    page.wait_for_selector(".search-list:not([hidden]) .search-opt", timeout=TIMEOUT)
    assert "SBER" in page.locator(".search-list:not([hidden])").inner_text()
    page.locator("#nav-search").press("ArrowDown")
    page.locator("#nav-search").press("Enter")
    page.wait_for_function("() => location.hash === '#/symbol/SBER'", timeout=TIMEOUT)
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
@pytest.mark.parametrize("live_mode, hash_", [
    (False, "#/"), (False, "#/tools"), (False, "#/tools/backtest"), (False, "#/docs"),
    (False, "#/portfolios"), (False, "#/portfolios/lazy-plus"),
    (True, "#/symbol/SBER"), (True, "#/symbol/SU26238RMFS4"),
])
def test_mobile_375_no_horizontal_scroll(browser, server, live_mode, hash_):
    s = Session(browser, server, live=live_mode, width=375)
    try:
        s.goto("" if live_mode else "?demo=1", hash_)
        if hash_.startswith("#/symbol"):
            _wait_blocks(s.page, 8)
        if hash_ == "#/portfolios":
            s.page.wait_for_selector("#pf-root table.pf-list tbody tr", timeout=TIMEOUT)
        if hash_.startswith("#/portfolios/"):
            s.page.wait_for_selector("#pf-repl", timeout=TIMEOUT)
        sw = s.page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw[0] <= sw[1], f"{hash_}: горизонтальная прокрутка {sw[0]} > {sw[1]}"
        wide = s.page.evaluate(
            "() => [...document.querySelectorAll('body *')].filter((e) => { const r = e.getBoundingClientRect();"
            " if (!(r.width > 0 && r.right > document.documentElement.clientWidth + 1)) return false;"
            " for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {"
            "   const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; }"
            " return true; })"
            ".slice(0, 5).map((e) => e.tagName + '#' + e.id + '.' + e.className)")
        assert not wide, f"{hash_}: элементы шире экрана: {wide}"
        assert s.page.locator("#nav-toggle").is_visible(), "на мобильной ширине меню — за кнопкой"
        s.check_clean()
    finally:
        s.close()
