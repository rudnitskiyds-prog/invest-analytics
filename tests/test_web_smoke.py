"""
Смоук-тест веб-версии (web/) в демо-режиме: статический сервер из корня репозитория +
Playwright (chromium). Сеть не нужна: все запросы не к локальному серверу блокируются
и считаются ошибкой.

Пропускается, если не установлен python-пакет playwright или браузер chromium.
"""
from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright не установлен")

IDLE = "() => !document.body.classList.contains('is-busy')"
TIMEOUT = 60_000


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


def test_web_demo_smoke(server, browser):
    page = browser.new_page()
    errors: list[str] = []
    external: list[str] = []
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def route(r):
        if r.request.url.startswith(server):
            r.continue_()
        else:
            external.append(r.request.url)
            r.abort()

    page.route("**/*", route)
    try:
        page.goto(f"{server}/web/?demo=1", wait_until="load", timeout=TIMEOUT)
        assert page.locator("#demo-banner").is_visible(), "нет плашки демо-режима"
        page.wait_for_function(IDLE, timeout=TIMEOUT)
        assert page.locator("#engine-banner .notice-error").count() == 0, \
            page.locator("#engine-banner").inner_text()

        # Бэктест: в демо считается сразу; плюс явный запуск кнопкой
        page.get_by_role("tab", name="Бэктест").click()
        page.wait_for_selector("#bt-out table.metrics-table", timeout=TIMEOUT)
        _rerun(page, "Запустить бэктест", "#bt-out")
        _assert_table_and_chart(page, "#bt-out", "table.metrics-table")
        text = page.locator("#bt-out table.metrics-table").inner_text()
        assert "Коэф. Шарпа" in text and "Лежебока" in text, text[:500]

        # Граница Марковица
        page.get_by_role("tab", name="Граница Марковица").click()
        page.wait_for_selector("#fr-out canvas", timeout=TIMEOUT)
        _rerun(page, "Построить границу", "#fr-out")
        _assert_table_and_chart(page, "#fr-out")

        # Коэффициенты бумаги (по умолчанию MCFTR против MCFTR)
        page.get_by_role("tab", name="Коэффициенты бумаги").click()
        page.wait_for_selector("#sec-out table.metrics-table", timeout=TIMEOUT)
        _rerun(page, "Рассчитать", "#sec-out")
        _assert_table_and_chart(page, "#sec-out", "table.metrics-table")

        # Методика
        page.get_by_role("tab", name="Методика").click()
        assert page.locator("#panel-method").is_visible()
        assert page.locator("#formula-list .formula").count() >= 10
        page.wait_for_function(IDLE, timeout=TIMEOUT)

        assert not errors, "Ошибки в консоли:\n" + "\n".join(errors)
        assert not external, "Запросы во внешнюю сеть в демо-режиме:\n" + "\n".join(external)
    finally:
        page.close()
