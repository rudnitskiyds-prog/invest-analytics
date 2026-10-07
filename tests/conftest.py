"""Общие страховки набора тестов: RusETFs (rusetfs.com) в тестах недоступен.

Сборщик (scripts/collect_data.py) по умолчанию собирает и RusETFs. Если какой-то тест вызовет
main() без подмены источника, фабрика сессии ниже не даст уйти в сеть, а тест упадёт
на завершении с понятным сообщением. Тесты RusETFs (tests/test_rusetfs.py) подменяют фабрику сами.
"""
from __future__ import annotations

import pytest


class _NoNetworkSession:
    def __init__(self, calls: list):
        self.calls = calls

    def get(self, url, **kw):
        self.calls.append(url)
        raise AssertionError(f"тест обратился в сеть: GET {url}")


@pytest.fixture(autouse=True)
def _no_rusetfs_network(monkeypatch):
    try:
        from scripts import collect_data as cd
    except Exception:  # noqa: BLE001 — модуль может не импортироваться в окружении веб-тестов
        yield
        return
    calls: list = []
    monkeypatch.setattr(cd, "_rusetfs_session", lambda: _NoNetworkSession(calls))
    yield
    assert not calls, f"тест обратился к RusETFs без подмены: {calls}"
