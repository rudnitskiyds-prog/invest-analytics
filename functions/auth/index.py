"""Cloud Function `auth`: Basic-авторизация сайта в API Gateway.

Два режима одной функции:
* авторизатор (`x-yc-apigateway-authorizer`) на всех защищённых маршрутах -> {"isAuthorized": bool, "context": {...}};
* обычная интеграция на маршруте `/` (без авторизатора) -> 401 + `WWW-Authenticate: Basic`, чтобы браузер
  показал окно логина (отказ авторизатора шлюз отдаёт без этого заголовка), а после входа — 302 на /web/.
  Браузер запоминает логин для всего домена и дальше сам шлёт `Authorization` на /web/index.html, данные и /api/.
"""
import base64
import hashlib
import hmac
import os

LOGIN_PATHS = ("/",)
REALM = "InvestAnalytics"


ITERATIONS = 200_000


def _users() -> list:
    """SITE_USERS = "логин:соль_hex:pbkdf2_sha256_hex,..." (PBKDF2-HMAC-SHA256, 200 000 итераций)."""
    out = []
    for item in os.environ.get("SITE_USERS", "").split(","):
        parts = [p.strip() for p in item.strip().split(":")]
        if len(parts) == 3 and all(parts):
            try:
                out.append((parts[0].encode(), bytes.fromhex(parts[1]), parts[2].lower().encode()))
            except ValueError:
                continue
    return out


def _header(event, name: str) -> str:
    for k, v in (event.get("headers") or {}).items():
        if k.lower() == name:
            return v if isinstance(v, str) else ""
    return ""


def check_basic(authorization: str):
    """-> логин при верных данных, иначе None. Сравнение — постоянное по времени по всем пользователям."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        login, sep, password = base64.b64decode(token.strip(), validate=True).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    if not sep:
        return None
    login_b = login.encode()
    match = None
    for user in _users():
        if hmac.compare_digest(user[0], login_b):
            match = user
    # Для неизвестного логина считаем хэш с пустой солью — время ответа то же.
    salt, expected = (match[1], match[2]) if match else (b"", b"0" * 64)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex().encode()
    return login if hmac.compare_digest(digest, expected) and match else None


def _login_page(event) -> dict:
    if check_basic(_header(event, "authorization")):
        return {"statusCode": 302, "headers": {"Location": "/web/index.html", "Cache-Control": "no-store"}, "body": ""}
    return {
        "statusCode": 401,
        "headers": {"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"',
                    "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"},
        "body": "<!doctype html><meta charset=utf-8><title>Вход</title>"
                "<p>Для доступа к FinServ нужны логин и пароль. "
                "<a href=\"/\">Попробовать снова</a></p>",
    }


def handler(event, context):
    # Авторизатор на "/" не висит, поэтому путь "/" означает режим страницы входа.
    if event.get("path") in LOGIN_PATHS or (event.get("url") or "").split("?")[0] in LOGIN_PATHS:
        return _login_page(event)
    login = check_basic(_header(event, "authorization"))
    # В журнал ничего не пишем: логин — персональные данные.
    return {"isAuthorized": login is not None, "context": {"user": login} if login else {}}
