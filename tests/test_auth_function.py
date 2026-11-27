"""Cloud Function `auth` (functions/auth/index.py): Basic-авторизация сайта; два режима — авторизатор и страница входа."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "auth_index", Path(__file__).resolve().parent.parent / "functions" / "auth" / "index.py")
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)


FAST = 1000   # итераций в тестах (в коде — auth.ITERATIONS = 200 000)


def _entry(login: str, password: str, salt: bytes, iterations: int = FAST) -> str:
    """Строка SITE_USERS: логин:соль_hex:pbkdf2_sha256_hex."""
    return f"{login}:{salt.hex()}:{hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations).hex()}"


def _basic(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


@pytest.fixture(autouse=True)
def users(monkeypatch):
    # тестовые учётки, только для этого файла
    monkeypatch.setattr(auth, "ITERATIONS", FAST)
    boris = _entry("boris", "p:w2", b"\x02" * 16)
    login, salt, digest = boris.split(":")
    monkeypatch.setenv("SITE_USERS", ", ".join([
        _entry("anna", "пароль-1", b"\x01" * 16), f" {login}:{salt}:{digest.upper()} ",
        "broken", ":00:nohash", "carl:не-hex:abcd", "dan:00", _entry("eve", "", b"\x03" * 16)]))


@pytest.mark.parametrize("header, expected", [
    (_basic("anna", "пароль-1"), "anna"),
    (_basic("boris", "p:w2"), "boris"),                # двоеточие в пароле, хэш в верхнем регистре
    ("basic " + _basic("anna", "пароль-1").split()[1], "anna"),   # схема без учёта регистра
    (_basic("anna", "пароль-2"), None),
    (_basic("anna", "p:w2"), None),                     # чужой пароль
    (_basic("boris", "пароль-1"), None),
    (_basic("carl", "пароль-1"), None),
    (_basic("broken", ""), None),
    (_basic("", "nohash"), None),
    (_basic("carl", ""), None), (_basic("dan", ""), None),
    (_basic("eve", ""), "eve"),                         # пустой пароль — это тоже пароль
    ("Basic !!!не-base64!!!", None),
    ("Basic " + base64.b64encode(b"\xff\xfe:x").decode(), None),   # не UTF-8
    ("Basic " + base64.b64encode(b"anna").decode(), None),        # без двоеточия
    ("Basic ", None), ("Bearer abc", None), ("", None), (None, None),
])
def test_check_basic(header, expected):
    assert auth.check_basic(header) == expected


def test_no_users_configured(monkeypatch):
    monkeypatch.delenv("SITE_USERS")
    assert auth.check_basic(_basic("anna", "пароль-1")) is None


def _ev(path="/{path+}", authorization=None, header_name="Authorization"):
    ev = {"path": path, "headers": {}}
    if authorization is not None:
        ev["headers"][header_name] = authorization
    return ev


def test_authorizer_mode():
    ok = auth.handler(_ev("/web/", _basic("anna", "пароль-1")), None)
    assert ok == {"isAuthorized": True, "context": {"user": "anna"}}
    # заголовок без учёта регистра имени
    assert auth.handler(_ev("/api/assistant", _basic("anna", "пароль-1"), "authorization"), None)["isAuthorized"]
    bad = auth.handler(_ev("/web/", _basic("anna", "нет")), None)
    assert bad == {"isAuthorized": False, "context": {}}
    assert auth.handler(_ev("/web/"), None)["isAuthorized"] is False
    assert auth.handler({}, None)["isAuthorized"] is False


def test_login_page_asks_password():
    r = auth.handler(_ev("/"), None)
    assert r["statusCode"] == 401
    assert r["headers"]["WWW-Authenticate"].startswith("Basic realm=")
    assert r["headers"]["Cache-Control"] == "no-store"
    assert "isAuthorized" not in r
    r = auth.handler(_ev("/", _basic("anna", "нет")), None)
    assert r["statusCode"] == 401 and "WWW-Authenticate" in r["headers"]


def test_login_page_redirects_after_login():
    r = auth.handler(_ev("/", _basic("boris", "p:w2")), None)
    assert r["statusCode"] == 302 and r["headers"]["Location"] == "/web/"


def test_login_page_by_url_field():
    r = auth.handler({"url": "/?x=1", "headers": {}}, None)
    assert r["statusCode"] == 401


def test_default_iterations():
    assert auth.ITERATIONS == FAST   # подменено фикстурой; исходное значение — из файла
    src = (Path(auth.__file__)).read_text(encoding="utf-8")
    assert "ITERATIONS = 200_000" in src


def test_real_iterations_entry(monkeypatch):
    """Строка, сгенерированная по инструкции README (200 000 итераций), принимается без подмены."""
    monkeypatch.setattr(auth, "ITERATIONS", 200_000)
    monkeypatch.setenv("SITE_USERS", _entry("ivan", "секрет", b"\x07" * 16, 200_000))
    assert auth.check_basic(_basic("ivan", "секрет")) == "ivan"
    assert auth.check_basic(_basic("ivan", "секреТ")) is None


def test_salt_matters(monkeypatch):
    line = _entry("anna", "пароль-1", b"\x01" * 16)
    login, _, digest = line.split(":")
    monkeypatch.setenv("SITE_USERS", f"{login}:{(b'\x09' * 16).hex()}:{digest}")
    assert auth.check_basic(_basic("anna", "пароль-1")) is None
