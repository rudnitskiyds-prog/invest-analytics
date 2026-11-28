"""Сертификат Минцифры для ISS: iss.ca_verify() и передача verify= в запросы ISS (без сети)."""
from __future__ import annotations

import pytest

from core.data import iss, universe


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ISS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("TINVEST_CA_BUNDLE", raising=False)


def _pem(tmp_path, name):
    p = tmp_path / name
    p.write_text("-----BEGIN CERTIFICATE-----\n")
    return str(p)


def test_no_env_means_certifi_default():
    assert iss.ca_verify() is True


def test_iss_bundle_has_priority_over_tinvest(tmp_path, monkeypatch):
    a, b = _pem(tmp_path, "iss.pem"), _pem(tmp_path, "ti.pem")
    monkeypatch.setenv("ISS_CA_BUNDLE", a)
    monkeypatch.setenv("TINVEST_CA_BUNDLE", b)
    assert iss.ca_verify() == a


def test_falls_back_to_tinvest_bundle(tmp_path, monkeypatch):
    b = _pem(tmp_path, "ti.pem")
    monkeypatch.setenv("TINVEST_CA_BUNDLE", b)
    assert iss.ca_verify() == b
    monkeypatch.setenv("ISS_CA_BUNDLE", "   ")          # пустая/пробельная переменная — как не заданная
    assert iss.ca_verify() == b


@pytest.mark.parametrize("var", ["ISS_CA_BUNDLE", "TINVEST_CA_BUNDLE"])
def test_missing_file_is_error_not_silent_fallback(tmp_path, monkeypatch, var):
    monkeypatch.setenv(var, str(tmp_path / "nope.pem"))
    with pytest.raises(iss.ISSError, match=var):
        iss.ca_verify()


def test_missing_iss_bundle_not_masked_by_valid_tinvest(tmp_path, monkeypatch):
    monkeypatch.setenv("ISS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("TINVEST_CA_BUNDLE", _pem(tmp_path, "ti.pem"))
    with pytest.raises(iss.ISSError):
        iss.ca_verify()


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": {"columns": [], "data": []}}


class _NoCache:
    def get(self, *a, **k):
        return None

    def put(self, *a, **k):
        pass


def test_get_json_passes_verify_from_env(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(iss, "_cache", _NoCache())
    monkeypatch.setattr(iss._session, "get", lambda url, **kw: seen.append(kw) or _Resp())
    iss.get_json("/ca-test-1")
    assert seen[-1]["verify"] is True
    bundle = _pem(tmp_path, "iss.pem")
    monkeypatch.setenv("ISS_CA_BUNDLE", bundle)
    iss.get_json("/ca-test-2")
    assert seen[-1]["verify"] == bundle
    monkeypatch.delenv("ISS_CA_BUNDLE")
    monkeypatch.setenv("TINVEST_CA_BUNDLE", bundle)    # читается при каждом запросе, не кэшируется
    iss.get_json("/ca-test-3")
    assert seen[-1]["verify"] == bundle


def test_get_json_missing_bundle_raises_iss_error(tmp_path, monkeypatch):
    monkeypatch.setattr(iss, "_cache", _NoCache())
    monkeypatch.setattr(iss.time, "sleep", lambda s: None)
    monkeypatch.setattr(iss._session, "get", lambda *a, **k: pytest.fail("запрос не должен уйти с битым пакетом"))
    monkeypatch.setenv("ISS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    with pytest.raises(iss.ISSError):
        iss.get_json("/ca-test-4", retries=1)


def test_host_available_passes_verify(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(universe, "_HOST_STATE", {})
    monkeypatch.setattr("requests.get", lambda url, **kw: seen.append(kw) or _Resp())
    assert universe.host_available("https://iss.example.test/x") is True
    assert seen[-1]["verify"] is True
    bundle = _pem(tmp_path, "iss.pem")
    monkeypatch.setenv("ISS_CA_BUNDLE", bundle)
    universe._HOST_STATE.clear()
    assert universe.host_available("https://iss.example.test/x") is True
    assert seen[-1]["verify"] == bundle


def test_host_available_5xx_and_error(monkeypatch):
    monkeypatch.setattr(universe, "_HOST_STATE", {})

    class R500(_Resp):
        status_code = 503
    monkeypatch.setattr("requests.get", lambda url, **kw: R500())
    assert universe.host_available("https://a.example.test/x") is False

    def boom(url, **kw):
        raise ConnectionError("tls")
    monkeypatch.setattr("requests.get", boom)
    assert universe.host_available("https://b.example.test/x") is False
