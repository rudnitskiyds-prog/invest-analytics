"""Смоук-тесты страницы «Пенсионная индустрия» (#/npf, web/CONTRACT.md). Живая сессия harness (tests/test_web_smoke.py),
данные — настоящие public/data/npf/ через статический сервер; сети нет. Эталон «активы под наблюдением» — из исходного
приложения (то же число проверяет tests/web/npf.test.mjs: резервы, отчёты о структуре, март 2026)."""
from __future__ import annotations

import json
import re

from tests.test_web_smoke import IDLE, TIMEOUT, Session, browser, live, server  # noqa: F401  (фикстуры)

BAD = re.compile(r"\b(NaN|undefined|null|Infinity)\b")
AUM_REF = "2 222 238 482 760,82"   # ₽, резервы · отчёты о структуре · мар 2026


def _norm(s: str) -> str:
    return re.sub(r"[\s  ]+", " ", s)


def _open(live, hash_="#/npf"):
    live.goto("", hash_)
    live.page.wait_for_selector("#npf-pane .kpis .kpi, #npf-pane .notice", timeout=TIMEOUT)
    return live.page


def _kpis(page) -> dict[str, str]:
    return {k.locator(".kpi-name").inner_text(): _norm(k.locator(".kpi-value").inner_text() + " | " + k.locator(".kpi-sub").inner_text()) for k in page.locator("#npf-pane .kpis .kpi").all()}


def _no_bad(page, sel="#npf-root"):
    text = page.locator(sel).inner_text()
    assert not BAD.findall(text), [ln for ln in text.splitlines() if BAD.search(ln)][:5]


def _hscroll(page) -> int:
    return page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth")


def _tab(page, name):
    page.get_by_role("tab", name=name, exact=True).first.click()


def test_npf_market_default_and_reference_kpi(live):
    page = _open(live)
    assert live.visible_panel() == ["panel-npf"]
    assert page.locator("a[data-nav=npf][aria-current=page]").count() == 1
    assert page.locator("#npf-root h2").inner_text() == "Пенсионная индустрия"
    head = _norm(page.locator(".npf-meta").inner_text())
    assert "Данные на" in head and "Раскрытие НПФ и Банк России" in head
    # режим по умолчанию: резервы, отчёты о структуре; вкладка «Рынок» выбрана
    assert page.locator("#npf-kind-0").get_attribute("aria-pressed") == "true"
    assert page.locator("#npf-src-struct").get_attribute("aria-pressed") == "true"
    assert page.locator("#npf-tab-market").get_attribute("aria-selected") == "true"
    assert page.locator("#npf-pane").get_attribute("aria-labelledby") == "npf-tab-market"
    # эталон: март 2026
    page.select_option("#npf-m-month", "2026-03")
    page.wait_for_function("() => document.querySelector('#npf-m-month').value === '2026-03' && document.querySelector('#npf-pane .kpi-sub')?.textContent.includes('2\\u00a0222')", timeout=TIMEOUT)
    k = _kpis(page)
    assert AUM_REF in k["Активы под наблюдением"], k["Активы под наблюдением"]
    assert k["Активы под наблюдением"].startswith("2,22 трлн ₽"), k
    # график, тепловая карта, топ фондов, сортировка
    assert page.locator("#npf-pane canvas").count() == 1
    rows = page.locator("#npf-pane table.npf-heat tbody tr").count()
    assert rows >= 30
    first = page.locator("#npf-pane table.npf-heat tbody tr th a").first.inner_text()
    page.click("#npf-sort-name")
    assert page.locator("#npf-sort-name").evaluate("(b) => b === document.activeElement"), "фокус остаётся на заголовке после сортировки"
    assert page.locator("th[aria-sort=ascending]").count() == 1
    assert page.locator("#npf-pane table.npf-heat tbody tr th a").first.inner_text() != first or rows == 1
    _no_bad(page)
    live.check_clean()


def test_npf_fund_page_compare_and_tabs_keyboard(live):
    page = _open(live)
    page.locator("#npf-pane table.npf-heat tbody tr th a", has_text="НПФ Сбербанка").first.click()
    page.wait_for_selector("#npf-ftab-overview", timeout=TIMEOUT)
    assert live.hash() == "#/npf/fund/sber"
    assert page.locator(".npf-fundname").inner_text() == "НПФ Сбербанка"
    k = _kpis(page)
    assert "АУМ · активы до обязательств" in k and "СЧА · чистые активы" in k, list(k)
    assert page.locator("#npf-pane canvas").count() >= 2     # структура по датам, кольцо, стоимость
    # вкладки фонда: стрелки и Home/End, роль tabpanel
    page.locator("#npf-ftab-overview").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("#npf-ftab-holdings[aria-selected=true]", timeout=TIMEOUT)
    assert page.locator("#npf-ftab-holdings").evaluate("(b) => b === document.activeElement")
    page.wait_for_selector("#npf-fpane table tbody tr", timeout=TIMEOUT)
    page.fill("#npf-h-q", "ОФЗ")
    page.wait_for_function("() => /из \\d+ строк/.test(document.querySelector('#npf-fpane [role=status]').textContent)", timeout=TIMEOUT)
    assert page.locator("#npf-h-q").evaluate("(b) => b === document.activeElement"), "фокус остаётся в поле поиска"
    for name in ("Эмитенты", "Сравнение дат", "Сверка со структурой", "Источники"):
        _tab(page, name)
        page.wait_for_selector("#npf-fpane table", timeout=TIMEOUT)
        assert page.locator("#npf-fpane .notice-error").count() == 0
    _no_bad(page)
    # возврат по «назад» ведёт на рынок
    page.go_back()
    page.wait_for_selector("#npf-pane table.npf-heat", timeout=TIMEOUT)
    assert live.hash() == "#/npf"
    # сравнение фондов
    _tab(page, "Сравнение фондов")
    page.wait_for_selector("#npf-pane .npf-chips", timeout=TIMEOUT)
    assert live.hash() == "#/npf/compare"
    assert page.locator(".npf-chip[aria-pressed=true]").count() == 5
    assert page.locator("#npf-pane canvas").count() == 1 and page.locator("#npf-pane table tbody tr").count() > 3
    page.locator(".npf-chip[aria-pressed=false]").first.click()
    page.wait_for_function("() => document.querySelectorAll('.npf-chip[aria-pressed=true]').length === 6", timeout=TIMEOUT)
    assert page.locator(".npf-chip[aria-pressed=true]").count() == 6
    _no_bad(page)
    live.check_clean()


def test_npf_search_issuer_and_kind_switch(live):
    page = _open(live)
    page.fill("#npf-q", "Минфин")
    page.wait_for_selector("#npf-list [role=option]", timeout=TIMEOUT)
    assert page.locator("#npf-q").get_attribute("aria-expanded") == "true"
    assert "Минфин" in page.locator("#npf-list").inner_text()
    page.keyboard.press("ArrowDown")
    assert page.locator("#npf-q").get_attribute("aria-activedescendant") == "npf-opt-1"
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Enter")
    page.wait_for_selector("#npf-s-back", timeout=TIMEOUT)
    assert live.hash().startswith("#/npf/securities/")
    assert page.locator("#npf-pane canvas").count() == 1 and page.locator("#npf-pane table tbody tr").count() >= 1
    assert page.locator("#npf-pane .kpis .kpi").count() == 4
    _no_bad(page)
    page.click("#npf-s-back")
    page.wait_for_selector("#npf-pane .npf-grid2 table", timeout=TIMEOUT)
    assert live.hash() == "#/npf/securities"
    # поиск по тексту в самом разделе
    page.fill("#npf-s-q", "Минфин")
    page.wait_for_function("() => /по запросу/i.test(document.querySelector('#npf-pane').textContent)", timeout=TIMEOUT)
    assert page.locator("#npf-s-q").evaluate("(b) => b === document.activeElement")
    # вид средств: накопления и «вместе»
    _tab(page, "Рынок")
    page.wait_for_selector("#npf-pane .kpis", timeout=TIMEOUT)
    before = _kpis(page)["Активы под наблюдением"]
    page.click("#npf-kind-1")
    page.wait_for_function("() => document.querySelector('#npf-kind-1').getAttribute('aria-pressed') === 'true'", timeout=TIMEOUT)
    assert page.locator("#npf-kind-1").evaluate("(b) => b === document.activeElement")
    assert _kpis(page)["Активы под наблюдением"] != before
    page.click("#npf-kind-all")
    page.wait_for_function("() => document.querySelector('#npf-src-struct').disabled", timeout=TIMEOUT)
    assert page.locator("#npf-src-comp").get_attribute("aria-pressed") == "true"
    assert page.locator("#npf-pane canvas").count() == 1
    _no_bad(page)
    live.check_clean()


def test_npf_data_section(live):
    page = _open(live, "#/npf/data")
    page.wait_for_selector("#npf-pane .npf-cov", timeout=TIMEOUT)
    assert page.locator("#npf-tab-data").get_attribute("aria-selected") == "true"
    assert page.locator("#npf-pane .kpis .kpi").count() == 5
    assert page.locator(".npf-cov tbody tr").count() == 33
    assert page.locator("#npf-pane details.npf-det").count() >= 5
    with page.expect_download() as dl:
        page.click("#npf-d-struct")
    assert dl.value.suggested_filename == "npf-structure-all.csv"
    _no_bad(page)
    live.check_clean()


def test_npf_mobile_no_horizontal_scroll(browser, server):
    s = Session(browser, server, live=True, width=375)
    try:
        page = _open(s)
        assert _hscroll(page) <= 0, _hscroll(page)
        assert page.evaluate("() => { const w = document.querySelector('#npf-pane .table-wrap'); return w.scrollWidth > w.clientWidth; }"), "тепловая карта прокручивается внутри контейнера"
        for hash_ in ("#/npf/fund/sber", "#/npf/compare", "#/npf/securities", "#/npf/data"):
            s.goto("", hash_)
            page.wait_for_selector("#npf-pane .kpis, #npf-pane .npf-chips, #npf-pane table", timeout=TIMEOUT)
            assert _hscroll(page) <= 0, (hash_, _hscroll(page))
        s.check_clean()
    finally:
        s.close()


def test_npf_load_error_and_retry(live):
    page = live.page
    page.add_init_script("""(() => { const f = window.fetch; window.__fail = true;
        window.fetch = (u, ...a) => (window.__fail && String(u).includes('/npf/') ? Promise.reject(new TypeError('Failed to fetch')) : f(u, ...a)); })()""")
    live.goto("", "#/npf")
    page.wait_for_selector("#npf-retry", timeout=TIMEOUT)
    assert "Не удалось загрузить данные НПФ" in page.locator("#npf-root .notice-error").inner_text()
    page.evaluate("() => { window.__fail = false; }")
    page.click("#npf-retry")
    page.wait_for_selector("#npf-pane .kpis .kpi", timeout=TIMEOUT)
    assert page.locator("#npf-retry").count() == 0
    live.check_clean(allow=(r"Failed to fetch",))


def test_npf_fund_site_link_only_http(live):
    page = live.page

    def patch(r):   # подмена данных: у «sber» небезопасный адрес сайта
        body = json.loads(r.fetch().body())
        for f in body["funds"]:
            if f["id"] == "sber":
                f["url"] = "javascript:alert(1)"
        r.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
    page.route("**/public/data/npf/meta.json*", patch)
    live.goto("", "#/npf/fund/sber")
    page.wait_for_selector("#npf-ftab-overview", timeout=TIMEOUT)
    assert page.locator(".npf-fundname").inner_text() == "НПФ Сбербанка"
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator("a", has_text="Сайт фонда").count() == 0
    page.get_by_label("Выбрать фонд").select_option(index=0)   # у другого фонда ссылка на месте
    page.wait_for_selector("a.btn:has-text('Сайт фонда')", timeout=TIMEOUT)
    live.check_clean()


def test_npf_fund_site_link_attrs(live):
    page = _open(live, "#/npf/fund/sber")
    page.wait_for_selector("#npf-ftab-overview", timeout=TIMEOUT)
    links = page.locator("a.btn", has_text="Сайт фонда")
    if links.count():
        assert links.first.get_attribute("rel") == "noopener noreferrer" and links.first.get_attribute("target") == "_blank"
        assert re.match(r"https?://", links.first.get_attribute("href"), re.I)


def test_npf_bad_percent_and_unknown_fund(live):
    page = _open(live)
    page.evaluate("() => { location.hash = '#/npf/fund/100%25'; }")
    page.wait_for_selector("#npf-ftab-overview", timeout=TIMEOUT)
    assert "не найден" in page.locator("#npf-pane .notice-warn").inner_text()
    assert "Фонд «100%» не найден, показан" in page.locator("#npf-pane .notice-warn").inner_text()
    assert page.title().startswith("Пенсионная индустрия — Фонд")
    page.evaluate("() => { location.hash = '#/npf/nothing'; }")
    page.wait_for_selector("#npf-pane .notice-warn", timeout=TIMEOUT)
    assert "Раздел «nothing» не найден" in page.locator("#npf-pane .notice-warn").inner_text()
    assert page.title() == "Пенсионная индустрия — Рынок — FinServ"
    page.evaluate("() => { location.hash = '#/portfolio/100%25'; }")   # портфели: тот же разбор без второго decode
    live.check_clean()


def test_npf_timer_stops_when_leaving(live):
    page = _open(live, "#/npf/fund/sber")
    page.wait_for_selector("#npf-t-play", timeout=TIMEOUT)
    page.evaluate("""() => { window.__draws = 0; new MutationObserver(() => { window.__draws++; })
        .observe(document.querySelector('#npf-pane'), { childList: true, subtree: true }); }""")
    page.click("#npf-t-play")
    page.wait_for_function("() => window.__draws > 0", timeout=TIMEOUT)
    page.evaluate("() => { location.hash = '#/screener/stocks'; }")
    page.wait_for_function("() => document.querySelector('#panel-npf').hidden", timeout=TIMEOUT)
    page.wait_for_timeout(300)
    n = page.evaluate("() => window.__draws")
    page.wait_for_timeout(2000)    # тик таймера 800 мс: за это время были бы ещё перерисовки
    assert page.evaluate("() => window.__draws") == n
    assert page.title().startswith("Скринеры")


def test_npf_csv_qty_uses_comma(live):
    page = _open(live, "#/npf/fund/sber")
    page.wait_for_selector("#npf-ftab-overview", timeout=TIMEOUT)
    page.evaluate("""() => { window.__csv = []; const C = Blob; window.Blob = class extends C { constructor(p, o) { super(p, o); window.__csv.push(String(p[0])); } }; }""")
    page.get_by_role("tab", name="Состав", exact=False).first.click()
    btn = page.locator("#npf-h-csv")
    btn.wait_for(timeout=TIMEOUT)
    btn.click()
    csv = page.evaluate("() => window.__csv.at(-1)")
    head, *rows = csv.lstrip("﻿").split("\r\n")
    col = head.split(";").index("Количество")
    qtys = [r.split(";")[col] for r in rows if r]
    assert qtys and not any(re.fullmatch(r"-?\d+\.\d+", q) for q in qtys), qtys[:5]
    assert any("," in q for q in qtys) or all(re.fullmatch(r"-?\d*", q) for q in qtys)


def test_npf_focus_not_on_page_heading(live):
    live.goto("", "#/")
    page = live.page
    page.click("a[data-nav=npf]")
    page.wait_for_selector("#npf-pane .kpis .kpi", timeout=TIMEOUT)
    assert page.evaluate("() => document.activeElement.id") == "npf-pane"
    assert page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") == "none", "после клика мышью рамки нет"
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => document.activeElement.tagName") != "H2"
    page.evaluate("() => { location.hash = '#/docs'; }")
    page.wait_for_function("() => !document.querySelector('#panel-method').hidden")
    assert page.evaluate("() => getComputedStyle(document.querySelector('#panel-method h1, #panel-method h2')).outlineStyle") == "none"
