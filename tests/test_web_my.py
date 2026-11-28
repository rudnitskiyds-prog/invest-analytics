"""Смоук-тесты страниц «Мой портфель» (#/my) и «Ребалансировки» (#/my/rebalance), web/CONTRACT.md, раздел B.
Живая сессия на поддельном ISS (tests/iss_fake.py), логотипы Т-Банка — заглушка 1x1 (Session). Данные пользователя
хранятся в localStorage страницы (ключ finserv.portfolio.v1); каждая сессия — чистый профиль."""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from tests import iss_fake
from tests.test_web_smoke import IDLE, TIMEOUT, Session, browser, live, server  # noqa: F401  (фикстуры)

KEY = "finserv.portfolio.v1"
D_DEPOSIT = (dt.date.today() - dt.timedelta(days=200)).isoformat()
D_BUY = (dt.date.today() - dt.timedelta(days=190)).isoformat()
SBER_QTY, SBER_PRICE, LQDT_QTY, LQDT_PRICE, DEPOSIT = 100, 250.0, 1000, 1.0, 1_000_000.0
CASH = DEPOSIT - SBER_QTY * SBER_PRICE - LQDT_QTY * LQDT_PRICE


def _now_price(secid: str) -> float:
    """Текущая цена в поддельном ISS: LAST = последнее закрытие × 1,004."""
    return round(float(iss_fake.ohlc(secid)["close"].iloc[-1]) * 1.004, 6)


EXPECT_TOTAL = SBER_QTY * _now_price("SBER") + LQDT_QTY * _now_price("LQDT") + CASH


def _rub(text: str) -> float:
    """«1 003 456 ₽» -> 1003456.0"""
    t = re.sub(r"[^\d,.\-−]", "", text).replace("−", "-").replace(",", ".")
    return float(t)


def _open(live, hash_="#/my"):
    live.goto("", hash_)
    return live.page


def _new_portfolio(page, name="Основной"):
    page.fill("#pf-new", name)
    page.click(".pf-create button[type=submit]")
    page.wait_for_selector("#tx-date", state="attached", timeout=TIMEOUT)


def _add_tx(page, date, type_, secid="", qty="", price="", amount="", fee=""):
    page.locator(".my-details").first.evaluate("(e) => { e.open = true; }")
    page.fill("#tx-date", date)
    page.select_option("#tx-type", type_)
    for sel, v in (("#tx-secid", secid), ("#tx-qty", qty), ("#tx-price", price), ("#tx-amount", amount), ("#tx-fee", fee)):
        page.fill(sel, v)
    page.click(".tx-form button[type=submit]")
    page.wait_for_selector(".notice-ok", timeout=TIMEOUT)


def _stored(page):
    raw = page.evaluate(f"() => localStorage.getItem('{KEY}')")
    return json.loads(raw) if raw else None


def _no_bad_text(page, sel):
    text = page.locator(sel).inner_text()
    bad = re.findall(r"\b(NaN|undefined|null|Infinity)\b", text)
    assert not bad, f"в тексте {sel}: {bad}\n" + "\n".join(line for line in text.splitlines() if re.search(r"NaN|undefined|null|Infinity", line))[:1500]


def _fill_portfolio(page):
    _new_portfolio(page)
    _add_tx(page, D_DEPOSIT, "DEPOSIT", amount=str(int(DEPOSIT)))
    _add_tx(page, D_BUY, "BUY", "SBER", str(SBER_QTY), str(SBER_PRICE))
    _add_tx(page, D_BUY, "BUY", "LQDT", str(LQDT_QTY), str(LQDT_PRICE))
    page.wait_for_selector(".kpis", timeout=TIMEOUT)


def _wait_positions(page):
    page.wait_for_selector(".my-pane table.my-table tbody tr", timeout=TIMEOUT)


def _hscroll(page) -> int:
    return page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth")


# ================================================================ #/my
def test_my_starts_empty_and_localstorage_is_clean(live):
    page = _open(live)
    page.wait_for_selector("#my-root .my-empty", timeout=TIMEOUT)
    assert live.visible_panel() == ["panel-my"]
    assert page.locator("a[data-nav=my][aria-current=page]").count() == 1
    assert page.locator("#my-root h2").inner_text() == "Мой портфель"
    assert "хранятся только в вашем браузере" in page.locator("#my-root").inner_text()
    assert page.evaluate("() => localStorage.length") == 0, page.evaluate("() => Object.keys(localStorage)")
    assert page.locator("#pf-new").input_value() == "Основной", "для нового пользователя подставлено название"
    assert page.locator("#tx-date").count() == 0, "форма операции появляется после создания портфеля"
    live.check_clean()


def test_my_full_scenario(live):
    page = _open(live)
    _new_portfolio(page)
    assert _stored(page)["portfolios"][0]["name"] == "Основной"
    # пустой портфель: подсказка про первые шаги
    assert "сначала «Пополнение»" in page.locator(".my-body").inner_text()

    _add_tx(page, D_DEPOSIT, "DEPOSIT", amount=str(int(DEPOSIT)))
    _add_tx(page, D_BUY, "BUY", "sber", str(SBER_QTY), str(SBER_PRICE))        # сумма пустая = количество × цена; тикер приводится к верхнему регистру
    _add_tx(page, D_BUY, "BUY", "LQDT", str(LQDT_QTY), str(LQDT_PRICE), fee="0")
    page.wait_for_selector(".kpis", timeout=TIMEOUT)
    _wait_positions(page)

    # итоги: стоимость = бумаги по текущим ценам + свободные деньги, внесено нетто = пополнение
    kpis = {k.locator(".kpi-name").inner_text(): k.locator(".kpi-value").inner_text() for k in page.locator(".kpis .kpi").all()}
    assert abs(_rub(kpis["Стоимость портфеля"]) - EXPECT_TOTAL) <= 1, (kpis, EXPECT_TOTAL)
    assert _rub(kpis["Внесено (нетто)"]) == DEPOSIT
    assert abs(_rub(kpis["Свободные деньги"]) - CASH) <= 1
    assert _rub(kpis["Результат"]) == pytest.approx(EXPECT_TOTAL - DEPOSIT, abs=1)
    assert kpis["XIRR (годовая)"].strip() not in ("", "—"), "XIRR считается, когда у всех бумаг есть цена"

    # позиции: таблица с логотипами и диаграмма долей
    rows = page.evaluate("""() => [...document.querySelectorAll('.my-pane table.my-table tbody tr')].map((tr) => ({
        secid: tr.querySelector('th b').textContent, badge: !!tr.querySelector('.asset-badge'),
        cells: [...tr.querySelectorAll('td')].map((td) => td.textContent.trim())}))""")
    assert [r["secid"] for r in rows] == ["SBER", "LQDT"] and all(r["badge"] for r in rows), rows
    sber = rows[0]["cells"]
    assert sber[0] == str(SBER_QTY) and abs(_rub(sber[1]) - SBER_PRICE) < 1e-6 and abs(_rub(sber[2]) - _now_price("SBER")) < 0.01, sber
    assert abs(_rub(sber[3]) - SBER_QTY * _now_price("SBER")) <= 1
    assert page.locator(".my-pane canvas").count() >= 1, "диаграмма долей"
    _no_bad_text(page, "#my-root")

    # динамика и метрики, сравнение со стратегиями — без ошибок, с графиками и таблицами
    page.click("[data-tab=dyn]")
    page.wait_for_selector(".my-pane canvas", timeout=TIMEOUT)
    page.wait_for_function("() => document.querySelectorAll('.my-pane canvas').length >= 2 && !document.querySelector('.my-pane [role=status].muted')", timeout=TIMEOUT)
    assert page.locator(".my-pane .notice-error").count() == 0, page.locator(".my-pane").inner_text()[:800]
    page.wait_for_selector(".my-pane table.metrics-table, .my-pane table", timeout=TIMEOUT)
    _no_bad_text(page, ".my-pane")
    page.click("[data-tab=cmp]")
    page.wait_for_selector(".my-pane .pick-list input[type=checkbox]", timeout=TIMEOUT)
    page.wait_for_function("() => document.querySelector('.my-pane canvas') && !document.body.classList.contains('is-busy')", timeout=TIMEOUT)
    assert page.locator(".my-pane .notice-error").count() == 0, page.locator(".my-pane").inner_text()[:800]
    _no_bad_text(page, ".my-pane")

    # операции: таблица, удаление (подтверждение), сообщение
    page.click("[data-tab=ops]")
    page.wait_for_selector(".my-pane table tbody tr", timeout=TIMEOUT)
    assert page.locator(".my-pane table tbody tr").count() == 3
    page.once("dialog", lambda d: d.accept())
    page.locator(".my-pane table tbody tr").nth(0).get_by_role("button", name=re.compile("Удалить операцию")).click()
    page.wait_for_selector(".notice-ok:has-text('Операция удалена')", timeout=TIMEOUT)
    assert len(_stored(page)["transactions"]) == 2

    # экспорт CSV / JSON — скачивание файлов
    page.locator("details", has_text="Импорт и экспорт").evaluate("(e) => { e.open = true; }")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Экспорт CSV").click()
    csv = open(dl.value.path(), "rb").read().decode("utf-8")
    assert dl.value.suggested_filename == "portfolio.csv" and csv.startswith("﻿date,type,secid,qty,price,amount,fee,note\n")
    assert len(csv.strip().splitlines()) == 3                      # заголовок + 2 операции
    with page.expect_download() as dl:
        page.get_by_role("button", name="Экспорт JSON").click()
    exp = json.loads(open(dl.value.path(), encoding="utf-8").read())
    assert exp["version"] == 1 and [p["name"] for p in exp["portfolios"]] == ["Основной"] and len(exp["transactions"]) == 2

    # импорт CSV с ошибкой: понятное сообщение, ничего не добавлено
    bad = "date,type,secid,qty,price,amount,fee,note\n2025-01-10,DEPOSIT,,,,1000,,\n2025-01-11,XX,SBER,1,1,,,\n"
    page.set_input_files("#csv-file", {"name": "bad.csv", "mimeType": "text/csv", "buffer": bad.encode()})
    page.get_by_role("button", name="Импортировать").click()
    page.wait_for_selector("#my-root .notice-error", timeout=TIMEOUT)
    msg = page.locator("#my-root .notice-error").first.inner_text()
    assert "Строка 3" in msg and "неизвестный тип операции" in msg, msg
    assert len(_stored(page)["transactions"]) == 2
    # импорт без файла
    page.set_input_files("#csv-file", [])
    page.get_by_role("button", name="Импортировать").click()
    page.wait_for_selector("#my-root .notice-error:has-text('Сначала выберите файл')", timeout=TIMEOUT)
    # корректный CSV (;, запятая как десятичный разделитель)
    ok = "date;type;secid;qty;price;amount;fee;note\n2025-01-10;DEPOSIT;;;;5 000,50;;\n"
    page.set_input_files("#csv-file", {"name": "ok.csv", "mimeType": "text/csv", "buffer": ok.encode()})
    page.get_by_role("button", name="Импортировать").click()
    page.wait_for_selector(".notice-ok:has-text('Импортировано 1')", timeout=TIMEOUT)
    txs = _stored(page)["transactions"]
    assert len(txs) == 3 and any(t["amount"] == 5000.5 and t["type"] == "DEPOSIT" for t in txs)
    live.check_clean()


def test_my_form_validation_messages(live):
    page = _open(live)
    _new_portfolio(page)
    page.locator(".my-details").first.evaluate("(e) => { e.open = true; }")
    page.select_option("#tx-type", "BUY")
    page.fill("#tx-secid", "")
    page.fill("#tx-qty", "5")
    page.fill("#tx-price", "10")
    page.click(".tx-form button[type=submit]")
    page.wait_for_selector(".tx-form .notice-error", timeout=TIMEOUT)
    assert "нужен тикер" in page.locator(".tx-form .notice-error").inner_text()
    page.fill("#tx-secid", "SBER")
    page.fill("#tx-qty", "abc")
    page.click(".tx-form button[type=submit]")
    assert "неотрицательные числа" in page.locator(".tx-form .notice-error").inner_text()
    assert _stored(page)["transactions"] == []
    # повтор названия портфеля — понятная ошибка, без учёта регистра
    page.fill("#pf-new", "основной")
    page.click(".pf-create button[type=submit]")
    page.wait_for_selector("#pf-err .notice-error", timeout=TIMEOUT)
    assert "уже есть" in page.locator("#pf-err").inner_text()
    assert len(_stored(page)["portfolios"]) == 1
    live.check_clean()


def test_my_delete_portfolio_and_json_import(live):
    page = _open(live)
    _new_portfolio(page)
    _add_tx(page, D_DEPOSIT, "DEPOSIT", amount="1000")
    # JSON из другого браузера: замена данных (подтверждение), затем удаление портфеля
    doc = {"version": 1, "nextId": 5, "portfolios": [{"id": 1, "name": "Из файла", "created": "2025-01-01", "benchmark": "MCFTR", "target": {}, "rebalance": "A", "band": 0.05, "last_rebalance": None}],
           "transactions": [{"id": 2, "portfolio_id": 1, "date": "2025-01-02", "type": "DEPOSIT", "secid": None, "qty": 0, "price": 0, "amount": 777, "fee": 0, "note": ""}]}
    page.locator("details", has_text="Импорт и экспорт").evaluate("(e) => { e.open = true; }")
    page.on("dialog", lambda d: d.accept())
    page.set_input_files("#json-file", {"name": "p.json", "mimeType": "application/json", "buffer": json.dumps(doc).encode()})
    page.get_by_role("button", name="Загрузить из JSON").click()
    page.wait_for_selector(".notice-ok:has-text('Данные загружены')", timeout=TIMEOUT)
    assert [p["name"] for p in _stored(page)["portfolios"]] == ["Из файла"]
    # не JSON — понятное сообщение
    page.locator("details", has_text="Импорт и экспорт").evaluate("(e) => { e.open = true; }")
    page.set_input_files("#json-file", {"name": "x.json", "mimeType": "application/json", "buffer": b"not json"})
    page.get_by_role("button", name="Загрузить из JSON").click()
    page.wait_for_selector("#my-root .notice-error:has-text('не JSON')", timeout=TIMEOUT)
    page.get_by_role("button", name="Удалить портфель").click()
    page.wait_for_selector(".my-empty", timeout=TIMEOUT)
    assert _stored(page)["portfolios"] == [] and _stored(page)["transactions"] == []
    live.check_clean()


def test_my_works_when_localstorage_is_blocked(browser, server):
    s = Session(browser, server, live=True)
    s.page.add_init_script("Storage.prototype.setItem = function () { throw new Error('QuotaExceededError'); };")
    try:
        s.goto("", "#/my")
        page = s.page
        _new_portfolio(page)
        assert "пропадёт при её закрытии" in page.locator("#my-root").inner_text()
        _add_tx(page, D_DEPOSIT, "DEPOSIT", amount="1000")
        page.wait_for_selector(".kpis", timeout=TIMEOUT)
        assert page.evaluate("() => localStorage.length") == 0
        s.check_clean()
    finally:
        s.close()


# ================================================================ #/my/rebalance
def test_rebalance_without_portfolio(live):
    page = _open(live, "#/my/rebalance")
    page.wait_for_selector("#rebalance-root h2", timeout=TIMEOUT)
    assert live.visible_panel() == ["panel-rebalance"]
    assert page.locator("a[data-nav=my][aria-current=page]").count() == 1
    assert "Сначала создайте портфель" in page.locator("#rebalance-root").inner_text()
    assert page.locator("#rebalance-root a[href='#/my']").count() == 1
    live.check_clean()


def test_rebalance_full_scenario(live):
    page = _open(live)
    _fill_portfolio(page)
    page.click("a[href='#/my/rebalance']") if page.locator("a[href='#/my/rebalance']").count() else page.evaluate("() => { location.hash = '#/my/rebalance'; }")
    page.wait_for_selector("#rb-rule", timeout=TIMEOUT)
    assert live.visible_panel() == ["panel-rebalance"]
    # редактор целей заполнен бумагами портфеля поровну; без целей — подсказка
    assert page.locator("input.rb-tk").count() == 2
    assert [page.locator("input.rb-tk").nth(i).input_value() for i in range(2)] == ["LQDT", "SBER"]
    assert "100" in page.locator("#rb-sum").inner_text()
    # цели 60/40, ежеквартально, коридор 5 п.п.
    for i, (tk, pc) in enumerate((("SBER", "60"), ("LQDT", "40"))):
        page.locator("input.rb-tk").nth(i).fill(tk)
        page.locator("input.rb-pct").nth(i).fill(pc)
    page.select_option("#rb-rule", "Q")
    page.fill("#rb-band", "5")
    page.get_by_role("button", name="Сохранить правило").click()
    page.wait_for_selector(".notice-ok:has-text('Правило сохранено')", timeout=TIMEOUT)
    pf = _stored(page)["portfolios"][0]
    assert pf["target"] == pytest.approx({"SBER": 0.6, "LQDT": 0.4}) and pf["rebalance"] == "Q" and pf["band"] == pytest.approx(0.05)

    # ближайшие даты: 8 строк по кварталам, кнопка .ics
    page.wait_for_selector("h3:has-text('Ближайшие даты')", timeout=TIMEOUT)
    dates = page.locator("section.card:has(h3:has-text('Ближайшие даты')) tbody tr")
    assert dates.count() == 8
    with page.expect_download() as dl:
        page.get_by_role("button", name="Добавить в календарь (.ics)").click()
    ics = open(dl.value.path(), "rb").read().decode("utf-8")   # байты: CRLF не должен превратиться в LF
    assert dl.value.suggested_filename == "rebalance_Основной.ics"
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR") and ics.count("BEGIN:VEVENT") == 8
    assert not re.search(r"(?<!\r)\n", ics) and "SUMMARY:Ребалансировка — Основной" in ics
    months = re.findall(r"DTSTART;VALUE=DATE:\d{4}(\d{2})\d{2}", ics)
    assert set(months) <= {"03", "06", "09", "12"}, months

    # отклонения и заявки
    page.wait_for_selector("section.card:has(h3:has-text('Отклонение')) table tbody tr", timeout=TIMEOUT)
    assert page.locator("section.card:has(h3:has-text('Отклонение')) tbody tr").count() == 2
    assert page.locator("section.card:has(h3:has-text('Отклонение')) canvas").count() == 1
    assert page.locator("section.card:has(h3:has-text('Отклонение')) .notice").count() == 1
    page.wait_for_selector("section.card:has(h3:has-text('Заявки')) table tbody tr", timeout=TIMEOUT)
    orders = page.locator("section.card:has(h3:has-text('Заявки')) tbody tr")
    actions = [orders.nth(i).locator("td").first.inner_text() for i in range(orders.count())]
    assert orders.count() == 2 and set(actions) <= {"Купить", "Продать", "—"}, actions
    assert actions == sorted(actions, key=["Продать", "Купить", "—"].index), "порядок: продать → купить → —"
    hint_with = page.locator("section.card:has(h3:has-text('Заявки')) .hint").last.inner_text()
    assert "Покупки" in hint_with and "округлено вниз до целых лотов" in hint_with
    # свободные деньги (974 тыс. ₽) учитываются только при включённом переключателе
    page.uncheck("#rb-cash")
    page.wait_for_function("(old) => document.querySelector(\"section.card:has(h3) .hint:last-child\") !== null && "
                           "[...document.querySelectorAll('.card .hint')].some((h) => h.textContent.includes('Покупки') && h.textContent !== old)",
                           arg=hint_with, timeout=TIMEOUT)
    # комиссия: нечисло — понятная ошибка; число — пересчёт
    page.fill("#rb-comm", "abc")
    page.locator("#rb-comm").dispatch_event("change")
    page.wait_for_selector("section.card:has(h3:has-text('Заявки')) .notice-error", timeout=TIMEOUT)
    assert "от 0 до 1 процента" in page.locator("section.card:has(h3:has-text('Заявки')) .notice-error").inner_text()
    page.fill("#rb-comm", "0,1")
    page.locator("#rb-comm").dispatch_event("change")
    page.wait_for_selector("section.card:has(h3:has-text('Заявки')) tbody tr", timeout=TIMEOUT)
    # «выполнено»
    page.get_by_role("button", name="Отметить ребалансировку выполненной").click()
    page.wait_for_selector(".notice-ok:has-text('Дата сохранена')", timeout=TIMEOUT)
    assert _stored(page)["portfolios"][0]["last_rebalance"] == dt.date.today().isoformat()
    assert "Последняя ребалансировка" in page.locator("#rebalance-root").inner_text()
    _no_bad_text(page, "#rebalance-root")
    live.check_clean()


def test_rebalance_validation_and_no_rule(live):
    page = _open(live)
    _fill_portfolio(page)
    page.evaluate("() => { location.hash = '#/my/rebalance'; }")
    page.wait_for_selector("#rb-rule", timeout=TIMEOUT)
    assert "Задайте целевые доли" in page.locator(".rb-rest").inner_text()
    page.locator("input.rb-pct").first.fill("-5")
    page.get_by_role("button", name="Сохранить правило").click()
    page.wait_for_selector("section.card .notice-error", timeout=TIMEOUT)
    assert "неотрицательное число" in page.locator("section.card .notice-error").first.inner_text()
    page.locator("input.rb-pct").first.fill("50")
    page.fill("#rb-band", "99")
    page.get_by_role("button", name="Сохранить правило").click()
    assert "от 0 до 50" in page.locator("section.card .notice-error").first.inner_text()
    page.fill("#rb-band", "5")
    page.select_option("#rb-rule", "none")
    page.get_by_role("button", name="Сохранить правило").click()
    page.wait_for_selector(".notice-ok:has-text('Правило сохранено')", timeout=TIMEOUT)
    page.wait_for_selector("h3:has-text('Ближайшие даты')", timeout=TIMEOUT)
    assert "плановых дат нет" in page.locator("#rebalance-root").inner_text()
    assert page.get_by_role("button", name="Добавить в календарь (.ics)").count() == 0
    live.check_clean()


# ================================================================ мобильная вёрстка
SEED = {"version": 1, "nextId": 20, "portfolios": [
    {"id": 1, "name": "Основной портфель с довольно длинным названием", "created": D_DEPOSIT, "benchmark": "MCFTR",
     "target": {"SBER": 0.6, "LQDT": 0.4}, "rebalance": "Q", "band": 0.05, "last_rebalance": None}],
    "transactions": [
        {"id": 2, "portfolio_id": 1, "date": D_DEPOSIT, "type": "DEPOSIT", "secid": None, "qty": 0, "price": 0, "amount": DEPOSIT, "fee": 0, "note": ""},
        {"id": 3, "portfolio_id": 1, "date": D_BUY, "type": "BUY", "secid": "SBER", "qty": SBER_QTY, "price": SBER_PRICE, "amount": SBER_QTY * SBER_PRICE, "fee": 12.5, "note": ""},
        {"id": 4, "portfolio_id": 1, "date": D_BUY, "type": "BUY", "secid": "LQDT", "qty": LQDT_QTY, "price": LQDT_PRICE, "amount": LQDT_QTY * LQDT_PRICE, "fee": 0, "note": ""}]}


@pytest.mark.parametrize("hash_, ready", [("#/my", "#my-root .kpis"), ("#/my/rebalance", "#rebalance-root section.card:has(h3:has-text('Заявки')) tbody tr")])
def test_mobile_375_no_horizontal_scroll(browser, server, hash_, ready):
    s = Session(browser, server, live=True, width=375)
    s.page.add_init_script(f"if (!localStorage.getItem('{KEY}')) localStorage.setItem('{KEY}', {json.dumps(json.dumps(SEED))});")
    try:
        s.goto("", hash_)
        page = s.page
        page.wait_for_selector(ready, timeout=TIMEOUT)
        page.wait_for_function(IDLE, timeout=TIMEOUT)
        page.wait_for_timeout(500)
        assert _hscroll(page) <= 0, f"горизонтальная прокрутка {hash_}: +{_hscroll(page)} px"
        if hash_ == "#/my":
            for tab in ("dyn", "cmp", "ops"):                       # остальные вкладки на узком экране
                page.click(f"[data-tab={tab}]")
                page.wait_for_timeout(600)
                page.wait_for_function("() => !document.body.classList.contains('is-busy')", timeout=TIMEOUT)
                assert _hscroll(page) <= 0, f"вкладка {tab}: +{_hscroll(page)} px"
            page.locator("details", has_text="Импорт и экспорт").evaluate("(e) => { e.open = true; }")
            page.locator(".my-details").first.evaluate("(e) => { e.open = true; }")
            assert _hscroll(page) <= 0, "раскрытые формы"
        s.check_clean()
    finally:
        s.close()
