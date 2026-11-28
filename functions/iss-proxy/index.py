"""Cloud Function `iss-proxy`: GET /iss/{path+} -> iss.moex.com, GET /cbr/{path+} -> www.cbr.ru. Контракт — web/CONTRACT.md (A)."""
import base64
import json
import re
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, unquote

import requests

CA_BUNDLE = str(Path(__file__).parent / "russian_trusted_ca.pem")  # УЦ Минцифры: iss.moex.com
# префикс -> (хост+префикс пути, белый список путей, проверка сертификата)
TARGETS = {
    "iss": ("https://iss.moex.com/iss/",
            ("engines/", "history/engines/", "securities/", "statistics/engines/", "markets/"),
            ("index.json", "securities.json"), CA_BUNDLE),
    "cbr": ("https://www.cbr.ru/", (), ("scripts/XML_daily.asp", "scripts/XML_dynamic.asp"), True),
}
MAX_PATH, MAX_QUERY, MAX_VALUE = 300, 1500, 1500
MAX_BYTES = 2_500_000  # base64 раздувает на 1/3: ответ шлюза ≤ ~3,5 МБ
CACHE_ITEM_MAX = 500 * 1024   # крупные тела не кэшируем
CACHE_BYTES = 40 * 1024 * 1024
TIMEOUT = 20
TTL, CACHE_SIZE = 60, 200
_PATH_RE = re.compile(r"^[A-Za-z0-9_.+\-/%,~]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

_cache = OrderedDict()  # url -> (время, статус, Content-Type, тело)


def _resp(status, body, ctype="application/json; charset=utf-8"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    out = {"statusCode": status, "headers": {"Content-Type": ctype, "Cache-Control": "private, max-age=60"}}
    if isinstance(body, bytes):
        out["body"], out["isBase64Encoded"] = base64.b64encode(body).decode(), True
    else:
        out["body"] = body
    return out


def _err(status, msg):
    r = _resp(status, {"error": msg})
    if status >= 400:
        r["headers"]["Cache-Control"] = "no-store"
    return r


def _route_path(event):
    """-> (префикс 'iss'|'cbr', путь) или (None, None). Основной источник — pathParams/params['path'], запасной — path/url."""
    raw = event.get("path") or ""
    url = event.get("url") or ""
    full = raw or url.split("?", 1)[0]
    m = re.search(r"/(iss|cbr)/(.*)$", full) or re.search(r"/(iss|cbr)/(.*)$", url.split("?", 1)[0])
    if not m:
        return None, None
    param = None
    for key in ("pathParams", "params", "pathParameters"):
        d = event.get(key)
        if isinstance(d, dict) and isinstance(d.get("path"), str):
            param = d["path"]
            break
    return m.group(1), (param if param is not None else m.group(2))


def _query(event):
    """-> список (имя, значение) или None, если что-то недопустимо."""
    q = event.get("multiValueQueryStringParameters")
    if not isinstance(q, dict) or not q:
        q = event.get("queryStringParameters") or {}
    pairs = []
    for k, v in q.items():
        for x in (v if isinstance(v, list) else [v]):
            pairs.append((k, "" if x is None else str(x)))
    if sum(len(k) + len(v) + 2 for k, v in pairs) > MAX_QUERY:
        return None
    for k, v in pairs:
        if not _NAME_RE.match(k) or len(v) > MAX_VALUE or _CTRL_RE.search(v):
            return None
    return pairs


def _allowed(prefix, path):
    _, prefixes, exact, _v = TARGETS[prefix]
    return path in exact or any(path.startswith(p) for p in prefixes)


def _log(path, code):
    print(json.dumps({"path": path[:100], "status": code}, ensure_ascii=False))  # без query


def handler(event, context):
    if (event.get("httpMethod") or "GET").upper() != "GET":
        return _err(405, "Допустим только GET.")
    prefix, path = _route_path(event)
    if prefix is None or path is None:
        return _err(400, "Неверный адрес запроса.")
    # путь могли прислать закодированным: проверяем и декодированный вид
    dec = unquote(path)
    bad = (not path or len(path) > MAX_PATH or not _PATH_RE.match(path) or _CTRL_RE.search(dec)
           or ".." in dec or "//" in dec or "@" in dec or ":" in dec or "\\" in dec or "?" in dec or "#" in dec
           or dec.startswith("/"))
    if bad or not _allowed(prefix, dec):
        _log(path, 404)
        return _err(404, "Такой путь не разрешён.")
    pairs = _query(event)
    if pairs is None:
        _log(path, 400)
        return _err(400, "Недопустимые параметры запроса.")
    base, _p, _e, verify = TARGETS[prefix]
    url = base + quote(dec, safe="/,.+-_~")
    if pairs:
        url += "?" + "&".join(f"{quote(k, safe='._')}={quote(v, safe='')}" for k, v in pairs)

    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < TTL:
        _cache.move_to_end(url)
        _log(path, hit[1])
        return _resp(hit[1], hit[3], hit[2])
    try:
        r = requests.get(url, timeout=TIMEOUT, verify=verify, stream=True, allow_redirects=False,
                         headers={"User-Agent": "finserv-iss-proxy/1.0", "Accept": "*/*"})
        try:
            code = r.status_code
            ctype = r.headers.get("Content-Type", "")
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    _log(path, 502)
                    return _err(502, "Ответ источника слишком большой.")
        finally:
            r.close()
    except requests.RequestException as e:
        _log(path, 502)
        print(type(e).__name__)
        return _err(502, "ISS недоступен" if prefix == "iss" else "Сайт ЦБ недоступен")
    if code not in (200, 404):
        _log(path, 502)
        return _err(502, "ISS недоступен" if prefix == "iss" else "Сайт ЦБ недоступен")
    if prefix == "iss":
        ctype = "application/json; charset=utf-8"
    elif "xml" in ctype.lower():
        pass  # как у источника (windows-1251 в заголовке); байты не перекодируем
    else:
        ctype = "text/xml; charset=windows-1251"
    if len(body) <= CACHE_ITEM_MAX:
        _cache[url] = (now, code, ctype, body)
        while len(_cache) > CACHE_SIZE or sum(len(v[3]) for v in _cache.values()) > CACHE_BYTES:
            _cache.popitem(last=False)
    _log(path, code)
    return _resp(code, body, ctype)
