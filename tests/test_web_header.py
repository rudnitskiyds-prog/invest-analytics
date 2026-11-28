"""Шапка на ширинах вокруг порога бургера (1240 px): меню не наезжает на бренд и поиск, нет горизонтальной прокрутки."""
from __future__ import annotations

import pytest

from tests.test_web_smoke import Session, browser, server  # noqa: F401  (фикстуры)


@pytest.mark.parametrize("width", [1241, 1300, 1400, 1500])
def test_header_no_overlap(browser, server, width):
    s = Session(browser, server, live=True, width=width)
    try:
        s.goto("", "#/")
        s.page.evaluate("() => document.fonts.ready")
        r = s.page.evaluate("""() => {
            const q = (x) => document.querySelector(x).getBoundingClientRect();
            const items = [...document.querySelectorAll('#site-nav > ul > li')].map((e) => e.getBoundingClientRect());
            return { brand: q('.brand').right, navL: items[0].left, navR: items.at(-1).right, search: q('#nav-search').left,
                     toggle: getComputedStyle(document.querySelector('#nav-toggle')).display,
                     font: document.fonts.check('600 15px Montserrat'),
                     h: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth };
        }""")
        assert r["toggle"] == "none" and r["font"], r
        assert r["brand"] < r["navL"], r
        assert r["navR"] <= r["search"], r
        assert r["h"] <= 0, r
    finally:
        s.close()
