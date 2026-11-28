"""Cloud Function `iss-proxy` (functions/iss-proxy/): GET /iss/{path+} -> iss.moex.com, /cbr/{path+} -> www.cbr.ru.
Модуль грузится по пути с подменённым `requests` (сети нет); контракт — web/CONTRACT.md, раздел A."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import ssl
import sys
import types
from pathlib import Path

import pytest

FN = Path(__file__).resolve().parent.parent / "functions" / "iss-proxy"


class _RequestException(Exception):
    pass


class FakeResp:
    def __init__(self, status=200, body=b"{}", ctype="application/json", chunks=None):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    def iter_content(self, size):
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture
def proxy(monkeypatch):
    """(модуль index, список вызовов requests.get); requests подменён, кэш пуст."""
    calls: list = []
    fake = types.ModuleType("requests")
    fake.RequestException = _RequestException
    fake.responses = [FakeResp()]
    fake.exc = None

    def get(url, **kw):
        calls.append((url, kw))
        if fake.exc:
            raise fake.exc
        r = fake.responses[min(len(calls) - 1, len(fake.responses) - 1)]
        return r() if callable(r) else r

    fake.get = get
    monkeypatch.setitem(sys.modules, "requests", fake)
    spec = importlib.util.spec_from_file_location("iss_proxy_index", FN / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._cache.clear()
    mod.fake = fake
    mod.calls = calls
    return mod


def ev(prefix="iss", path="engines/stock/markets/shares/securities.json", query=None, method="GET", param=True, multi=None):
    e = {"httpMethod": method, "path": f"/{prefix}/{path}"}
    if param:
        e["pathParams"] = {"path": path}
    if query is not None:
        e["queryStringParameters"] = query
    if multi is not None:
        e["multiValueQueryStringParameters"] = multi
    return e


def body_of(r):
    return base64.b64decode(r["body"]) if r.get("isBase64Encoded") else r["body"]


# ---------------------------------------------------------------- белый список
@pytest.mark.parametrize("path", [
    "engines/stock/markets/shares/boards/TQBR/securities.json", "history/engines/stock/markets/index/securities/IMOEX.json",
    "securities.json", "securities/SBER.json", "statistics/engines/stock/markets/bonds/bondization/SU26238RMFS4.json",
    "index.json", "markets/", "engines/stock/markets/shares/securities/RUGBITR5%2B/candles.json",
])
def test_iss_allowed_paths(proxy, path):
    r = proxy.handler(ev("iss", path), None)
    assert r["statusCode"] == 200, r
    assert proxy.calls[0][0].startswith("https://iss.moex.com/iss/")


@pytest.mark.parametrize("path", ["unknown/x.json", "index.json/x", "index.jsonx", "rss/news", "engine/stock", "history/securities", "admin"])
def test_iss_not_whitelisted_404(proxy, path):
    r = proxy.handler(ev("iss", path), None)
    assert r["statusCode"] == 404 and not proxy.calls


@pytest.mark.parametrize("path", ["scripts/XML_daily.asp", "scripts/XML_dynamic.asp"])
def test_cbr_allowed_paths(proxy, path):
    r = proxy.handler(ev("cbr", path, {"date_req": "01/12/2025"}), None)
    assert r["statusCode"] == 200 and proxy.calls[0][0].startswith(f"https://www.cbr.ru/{path}?")


@pytest.mark.parametrize("path", ["scripts/XML_daily.asp/x", "scripts/XML_val.asp", "scripts/xml_daily.asp", "engines/stock/markets",
                                  "index.json", "securities.json", "scripts/"])
def test_cbr_not_whitelisted_404(proxy, path):
    assert proxy.handler(ev("cbr", path), None)["statusCode"] == 404 and not proxy.calls


def test_iss_whitelist_does_not_leak_to_cbr_and_back(proxy):
    assert proxy.handler(ev("cbr", "engines/stock/markets/shares/securities.json"), None)["statusCode"] == 404
    assert proxy.handler(ev("iss", "scripts/XML_daily.asp"), None)["statusCode"] == 404


# ---------------------------------------------------------------- запрещённые пути
@pytest.mark.parametrize("path", [
    "engines/../../etc/passwd", "engines/%2e%2e/x", "engines/%2E%2E/x", "engines/stock/..%2f..%2fx",
    "securities@evil.example/x", "securities/@evil", "http://evil.example/x", "securities/http://evil.example",
    "engines//stock", "engines/stock//markets", "securities%3Fx=1", "securities.json%3Fq=1", "securities/a%23b",
    "securities/a\\b", "/engines/stock", "securities/" + "a" * 300, "securities/a b", "securities/%00", "securities/%0d%0aX",
])
def test_forbidden_paths_404_without_request(proxy, path):
    r = proxy.handler(ev("iss", path), None)
    assert r["statusCode"] == 404, path
    assert not proxy.calls, "запрещённый путь не должен дойти до источника"
    assert json.loads(r["body"])["error"]


def test_path_length_boundary(proxy):
    ok = "securities/" + "a" * (300 - len("securities/"))
    assert len(ok) == 300 and proxy.handler(ev("iss", ok), None)["statusCode"] == 200
    assert proxy.handler(ev("iss", ok + "a"), None)["statusCode"] == 404


def test_cbr_forbidden_paths(proxy):
    for path in ("scripts/XML_daily.asp/../x", "scripts/%2e%2e/XML_daily.asp", "scripts/XML_daily.asp%3Fa=1"):
        assert proxy.handler(ev("cbr", path), None)["statusCode"] == 404, path
    assert not proxy.calls


def test_host_and_scheme_are_hardcoded(proxy):
    proxy.handler(ev("iss", "securities.json"), None)
    proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    assert [c[0].split("?")[0] for c in proxy.calls] == ["https://iss.moex.com/iss/securities.json",
                                                         "https://www.cbr.ru/scripts/XML_daily.asp"]


# ---------------------------------------------------------------- метод и запрос
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "post"])
def test_non_get_405(proxy, method):
    r = proxy.handler(ev("iss", "securities.json", method=method), None)
    assert r["statusCode"] == 405 and not proxy.calls and r["headers"]["Cache-Control"] == "no-store"


def test_missing_method_is_get_and_head_is_405(proxy):
    e = ev("iss", "securities.json")
    del e["httpMethod"]
    assert proxy.handler(e, None)["statusCode"] == 200
    assert proxy.handler(ev("iss", "securities.json", method="HEAD"), None)["statusCode"] == 405


def test_unrouted_event_400(proxy):
    assert proxy.handler({"httpMethod": "GET", "path": "/other/x"}, None)["statusCode"] == 400
    assert proxy.handler({"httpMethod": "GET"}, None)["statusCode"] == 400


@pytest.mark.parametrize("query", [
    {"a-b": "1"}, {"a b": "1"}, {"": "1"}, {"x": "v" * 1501}, {"x": "a\nb"}, {"x": "a\x00b"}, {"x": "\x7f"},
    {f"p{i}": "v" * 300 for i in range(6)},        # суммарно > 1500
])
def test_bad_query_400(proxy, query):
    r = proxy.handler(ev("iss", "securities.json", query), None)
    assert r["statusCode"] == 400 and not proxy.calls and r["headers"]["Cache-Control"] == "no-store"


def test_query_limits_boundaries(proxy):
    assert proxy.handler(ev("iss", "securities.json", {"x": "v" * 1400}), None)["statusCode"] == 200
    assert proxy.handler(ev("iss", "securities.json", {"iss.only": "securities,marketdata", "iss.meta": "off", "q": "Сбер"}), None)["statusCode"] == 200


def test_query_is_encoded_and_cannot_inject_params(proxy):
    proxy.handler(ev("iss", "securities.json", {"q": "a&b=c#d e", "iss.only": "x,y"}), None)
    url = proxy.calls[0][0]
    q = url.split("?", 1)[1]
    assert q.count("&") == 1 and "q=a%26b%3Dc%23d%20e" in q and "iss.only=x%2Cy" in q
    assert "#" not in url


def test_multi_value_query_preferred_and_repeated(proxy):
    proxy.handler(ev("iss", "securities.json", {"x": "ignored"}, multi={"x": ["1", "2"], "y": ["3"]}), None)
    assert proxy.calls[0][0].split("?", 1)[1] == "x=1&x=2&y=3"


def test_query_none_value_becomes_empty(proxy):
    proxy.handler(ev("iss", "securities.json", {"flag": None}), None)
    assert proxy.calls[0][0].endswith("?flag=")


def test_date_req_slashes_percent_encoded(proxy):
    proxy.handler(ev("cbr", "scripts/XML_daily.asp", {"date_req": "01/12/2025"}), None)
    assert proxy.calls[0][0] == "https://www.cbr.ru/scripts/XML_daily.asp?date_req=01%2F12%2F2025"


# ---------------------------------------------------------------- разбор pathParams / path / url
def test_path_params_and_fallback_give_same_url(proxy):
    p = "engines/stock/markets/shares/securities.json"
    q = {"iss.only": "securities"}
    proxy.handler(ev("iss", p, q, param=True), None)                                  # pathParams
    proxy.handler({"httpMethod": "GET", "path": f"/iss/{p}", "queryStringParameters": q}, None)   # запасной: path
    proxy.handler({"httpMethod": "GET", "url": f"/iss/{p}?iss.only=securities", "queryStringParameters": q}, None)   # запасной: url
    proxy.handler({"httpMethod": "GET", "path": f"/iss/{p}", "params": {"path": p}, "queryStringParameters": q}, None)
    proxy.handler({"httpMethod": "GET", "path": f"/iss/{p}", "pathParameters": {"path": p}, "queryStringParameters": q}, None)
    urls = {c[0] for c in proxy.calls}
    assert urls == {f"https://iss.moex.com/iss/{p}?iss.only=securities"}, urls
    assert len(proxy.calls) == 1, "остальные запросы взяты из кэша"


def test_path_params_take_priority_over_path(proxy):
    e = {"httpMethod": "GET", "path": "/iss/index.json", "pathParams": {"path": "securities.json"}}
    proxy.handler(e, None)
    assert proxy.calls[0][0] == "https://iss.moex.com/iss/securities.json"


def test_prefix_cannot_be_overridden_by_params(proxy):
    """Хост выбирает префикс маршрута (iss/cbr), а не параметр пути."""
    e = {"httpMethod": "GET", "path": "/cbr/scripts/XML_daily.asp", "pathParams": {"path": "securities.json"}}
    assert proxy.handler(e, None)["statusCode"] == 404 and not proxy.calls


def test_gateway_stage_prefix_before_route(proxy):
    e = {"httpMethod": "GET", "path": "/iss/securities.json", "url": "/iss/securities.json"}
    assert proxy.handler(e, None)["statusCode"] == 200


# ---------------------------------------------------------------- запрос к источнику
def test_requests_get_arguments(proxy):
    proxy.handler(ev("iss", "securities.json"), None)
    proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    (_, iss), (_, cbr) = proxy.calls
    pem = iss["verify"]
    assert isinstance(pem, str) and pem == proxy.CA_BUNDLE and Path(pem).name == "russian_trusted_ca.pem" and Path(pem).is_file()
    assert cbr["verify"] is True
    for kw in (iss, cbr):
        assert kw["timeout"] == 20 and kw["allow_redirects"] is False and kw["stream"] is True
        assert kw["headers"]["User-Agent"].startswith("finserv-iss-proxy")


def test_response_closed(proxy):
    r = FakeResp()
    proxy.fake.responses = [r]
    proxy.handler(ev("iss", "securities.json"), None)
    assert r.closed


# ---------------------------------------------------------------- ответы
def test_iss_200_json_passthrough_and_headers(proxy):
    data = json.dumps({"securities": {"data": [["Сбер"]]}}, ensure_ascii=False).encode()
    proxy.fake.responses = [FakeResp(200, data, "text/plain")]
    r = proxy.handler(ev("iss", "securities.json"), None)
    assert r["statusCode"] == 200 and r["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert r["headers"]["Cache-Control"] == "private, max-age=60"
    assert body_of(r) == data and json.loads(body_of(r).decode())["securities"]["data"] == [["Сбер"]]


def test_source_404_passed_as_is(proxy):
    proxy.fake.responses = [FakeResp(404, b"{}")]
    r = proxy.handler(ev("iss", "securities/NOPE.json"), None)
    assert r["statusCode"] == 404 and body_of(r) == b"{}"


def test_cbr_body_base64_windows_1251(proxy):
    xml = '<?xml version="1.0" encoding="windows-1251"?><ValCurs><Valute><Name>Доллар США</Name></Valute></ValCurs>'.encode("cp1251")
    proxy.fake.responses = [FakeResp(200, xml, "text/xml; charset=windows-1251")]
    r = proxy.handler(ev("cbr", "scripts/XML_daily.asp", {"date_req": "01/12/2025"}), None)
    assert r["isBase64Encoded"] is True and "windows-1251" in r["headers"]["Content-Type"].lower()
    assert base64.b64decode(r["body"]) == xml and base64.b64decode(r["body"]).decode("cp1251").count("Доллар США") == 1


def test_cbr_non_xml_content_type_forced_to_windows_1251(proxy):
    proxy.fake.responses = [FakeResp(200, b"<a/>", "text/html")]
    r = proxy.handler(ev("cbr", "scripts/XML_dynamic.asp"), None)
    assert r["headers"]["Content-Type"] == "text/xml; charset=windows-1251"


@pytest.mark.parametrize("code", [500, 502, 503, 429, 403, 301])
def test_source_error_codes_become_502_json(proxy, code):
    proxy.fake.responses = [FakeResp(code, b"<html>oops</html>", "text/html")]
    r = proxy.handler(ev("iss", "securities.json"), None)
    assert r["statusCode"] == 502 and json.loads(r["body"]) == {"error": "ISS недоступен"}
    assert "json" in r["headers"]["Content-Type"] and r["headers"]["Cache-Control"] == "no-store"
    assert "oops" not in r["body"]


def test_cbr_503_message(proxy):
    proxy.fake.responses = [FakeResp(503, b"x")]
    r = proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    assert r["statusCode"] == 502 and json.loads(r["body"]) == {"error": "Сайт ЦБ недоступен"}


def test_network_error_502_and_not_cached(proxy):
    proxy.fake.exc = _RequestException("boom")
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 502
    proxy.fake.exc = None
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 200
    assert len(proxy.calls) == 2


def test_source_errors_not_cached(proxy):
    proxy.fake.responses = [FakeResp(503), FakeResp(200, b'{"ok":1}')]
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 502
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 200


def test_size_limit_502(proxy):
    assert proxy.MAX_BYTES == 2_500_000
    proxy.fake.responses = [FakeResp(200, chunks=[b"x" * 1_000_000] * 3)]
    r = proxy.handler(ev("iss", "securities.json"), None)
    assert r["statusCode"] == 502 and "большой" in json.loads(r["body"])["error"]
    assert r["headers"]["Cache-Control"] == "no-store"


def test_size_limit_boundary_exact_ok(proxy):
    proxy.fake.responses = [FakeResp(200, chunks=[b"x" * proxy.MAX_BYTES])]
    r = proxy.handler(ev("iss", "securities.json"), None)
    assert r["statusCode"] == 200 and len(body_of(r)) == proxy.MAX_BYTES
    proxy._cache.clear()
    proxy.fake.responses = [FakeResp(200, chunks=[b"x" * proxy.MAX_BYTES, b"y"])]
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 502


def test_big_body_not_cached_small_is_(proxy):
    proxy.fake.responses = [FakeResp(200, b"x" * (500 * 1024 + 1))]
    assert proxy.handler(ev("iss", "securities.json"), None)["statusCode"] == 200
    assert not proxy._cache
    proxy.fake.responses = [FakeResp(200, b"x" * (500 * 1024))]
    proxy.handler(ev("iss", "securities/A.json"), None)
    assert len(proxy._cache) == 1


def test_cache_limited_by_total_bytes(proxy):
    proxy.fake.responses = [FakeResp(200, b"x" * (500 * 1024))]
    for i in range(100):
        proxy.handler(ev("iss", f"securities/B{i}.json"), None)
    assert sum(len(v[3]) for v in proxy._cache.values()) <= proxy.CACHE_BYTES
    assert len(proxy._cache) == proxy.CACHE_BYTES // (500 * 1024)


def test_oversize_response_closed_and_not_cached(proxy):
    r = FakeResp(200, chunks=[b"x" * (4 * 1024 * 1024)])
    proxy.fake.responses = [r]
    proxy.handler(ev("iss", "securities.json"), None)
    assert r.closed and not proxy._cache


# ---------------------------------------------------------------- Cache-Control
def test_cache_control_on_all_responses(proxy):
    ok = proxy.handler(ev("iss", "securities.json"), None)
    assert ok["headers"]["Cache-Control"] == "private, max-age=60"
    for r in (proxy.handler(ev("iss", "nope"), None), proxy.handler(ev("iss", "securities.json", method="POST"), None),
              proxy.handler(ev("iss", "securities.json", {"a-b": "1"}), None), proxy.handler({"path": "/x"}, None)):
        assert r["statusCode"] >= 400 and r["headers"]["Cache-Control"] == "no-store"


# ---------------------------------------------------------------- LRU-кэш
def test_cache_hit_without_second_request(proxy):
    proxy.fake.responses = [FakeResp(200, b'{"a":1}'), FakeResp(200, b'{"a":2}')]
    a = proxy.handler(ev("iss", "securities.json", {"q": "x"}), None)
    b = proxy.handler(ev("iss", "securities.json", {"q": "x"}), None)
    assert len(proxy.calls) == 1 and a == b
    proxy.handler(ev("iss", "securities.json", {"q": "y"}), None)    # другой query — другой ключ
    assert len(proxy.calls) == 2


def test_cache_key_is_per_host_prefix(proxy):
    proxy.handler(ev("iss", "index.json"), None)
    proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    assert len(proxy.calls) == 2 and len(proxy._cache) == 2


def test_cache_expires_after_ttl(proxy, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(proxy.time, "time", lambda: t[0])
    proxy.handler(ev("iss", "securities.json"), None)
    t[0] += proxy.TTL - 1
    proxy.handler(ev("iss", "securities.json"), None)
    assert len(proxy.calls) == 1
    t[0] += 2
    proxy.handler(ev("iss", "securities.json"), None)
    assert len(proxy.calls) == 2


def test_cache_limited_to_200_entries_lru(proxy):
    assert proxy.CACHE_SIZE == 200
    for i in range(200):
        proxy.handler(ev("iss", f"securities/S{i}.json"), None)
    proxy.handler(ev("iss", "securities/S0.json"), None)        # обращение к самой старой — она «свежая»
    assert len(proxy.calls) == 200
    for i in range(200, 205):
        proxy.handler(ev("iss", f"securities/S{i}.json"), None)
    assert len(proxy._cache) == 200
    n = len(proxy.calls)
    proxy.handler(ev("iss", "securities/S0.json"), None)        # осталась в кэше
    assert len(proxy.calls) == n
    proxy.handler(ev("iss", "securities/S1.json"), None)        # вытеснена
    assert len(proxy.calls) == n + 1


def test_cached_cbr_response_keeps_base64_and_type(proxy):
    xml = "Рубль".encode("cp1251")
    proxy.fake.responses = [FakeResp(200, xml, "text/xml; charset=windows-1251")]
    a = proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    b = proxy.handler(ev("cbr", "scripts/XML_daily.asp"), None)
    assert a == b and base64.b64decode(b["body"]) == xml and len(proxy.calls) == 1


# ---------------------------------------------------------------- журнал
def test_log_has_path_and_status_without_query(proxy, capsys):
    proxy.handler(ev("iss", "securities/SBER.json", {"q": "секретный-запрос"}), None)
    out = capsys.readouterr().out
    assert "securities/SBER.json" in out and "200" in out and "секретный" not in out and "q=" not in out


# ---------------------------------------------------------------- сертификаты Минцифры
PEM = FN / "russian_trusted_ca.pem"
ROOT_SHA = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"
SUB_SHA = "BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B"


def test_pem_two_certificates_lf_and_fingerprints():
    raw = PEM.read_bytes()
    assert b"\r" not in raw, "файл должен быть в LF"
    text = raw.decode("ascii")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----\n.*?\n-----END CERTIFICATE-----\n", text, re.S)
    assert len(blocks) == 2 and text.count("BEGIN CERTIFICATE") == 2 and text.count("END CERTIFICATE") == 2
    assert "-----END CERTIFICATE----------BEGIN" not in text, "сертификаты склеены без перевода строки"
    assert text.endswith("-----END CERTIFICATE-----\n") and "PRIVATE" not in text
    prints = {hashlib.sha256(ssl.PEM_cert_to_DER_cert(b)).hexdigest().upper() for b in blocks}
    assert prints == {ROOT_SHA, SUB_SHA}


def test_pem_loads_in_ssl_context():
    ctx = ssl.create_default_context(cafile=str(PEM))
    stats = ctx.cert_store_stats()
    assert stats["x509_ca"] >= 1 and stats["x509"] >= 2
