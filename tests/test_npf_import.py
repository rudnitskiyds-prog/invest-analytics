"""scripts/import_npf.py и реальные файлы public/data/npf/: структура, размеры, ссылки, детерминизм. Без сети."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import import_npf as imp

NPF = Path(__file__).resolve().parent.parent / "public" / "data" / "npf"


def _load_db() -> dict:
    man = json.loads((NPF / "manifest.json").read_text(encoding="utf-8"))
    db: dict = {"comp": []}
    for f in man["files"]:
        body = json.loads((NPF / f["name"]).read_text(encoding="utf-8"))
        if f["part"] == "comp":
            db["comp"] += body
        elif f["part"] == "meta":
            db.update(body)
        else:
            db[f["part"]] = body
    return db


@pytest.fixture(scope="module")
def db():
    return _load_db()


def test_manifest_matches_files():
    man = json.loads((NPF / "manifest.json").read_text(encoding="utf-8"))
    names = {f["name"] for f in man["files"]}
    assert names == {p.name for p in NPF.glob("*.json")} - {"manifest.json"}
    assert {"meta.json", "struct.json"} <= names and any(n.startswith("comp-") for n in names)
    for f in man["files"]:
        data = (NPF / f["name"]).read_bytes()
        assert len(data) == f["bytes"] <= imp.MAX_FILE
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
        assert len(json.loads(data)) >= 1
    assert man["built"] and man["monitorUpdated"]


def test_real_data_passes_validation(db):
    imp.validate(db)
    assert len(db["funds"]) == 33 and len(db["classes"]) == 17 and len(db["comp"]) == 2525 and len(db["struct"]) == 3829


def test_reimport_is_deterministic(db, tmp_path):
    imp.write(db, tmp_path)
    for p in NPF.glob("*.json"):
        assert (tmp_path / p.name).read_bytes() == p.read_bytes(), p.name
    assert {p.name for p in tmp_path.glob("*.json")} == {p.name for p in NPF.glob("*.json")}


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["comp"][0]["r"].__setitem__(0, 10**9), "inst"),
    (lambda d: d["comp"][0].__setitem__("f", 99), "comp\\[0\\].f"),
    (lambda d: d["struct"][0]["r"].__setitem__(0, 99), "struct\\[0\\].r"),
    (lambda d: d["inst"][0].__setitem__(3, -1), "inst\\[0\\]"),
    (lambda d: d.pop("aumNote"), "нет ключей"),
    (lambda d: d["comp"][0]["r"].append(1), "кратна 4"),
])
def test_validation_rejects_broken_refs(db, mutate, msg):
    bad = copy.deepcopy({k: db[k] for k in imp.KEYS if k in db})
    bad["comp"] = bad["comp"][:3]
    bad["struct"] = bad["struct"][:3]
    mutate(bad)
    with pytest.raises(ValueError, match=msg):
        imp.validate(bad)


def test_extract_db_requires_script_tag():
    with pytest.raises(ValueError):
        imp.extract_db("<html></html>")
    assert imp.extract_db('<script id="database" type="application/json">{"a":1}</script>') == {"a": 1}


def _small(db):
    bad = copy.deepcopy({k: db[k] for k in imp.KEYS if k in db})
    bad["comp"], bad["struct"] = bad["comp"][:3], bad["struct"][:3]
    return bad


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["comp"][0].__setitem__("d", "2026-3-31"), "comp\\[0\\].d"),
    (lambda d: d["struct"][0].__setitem__("d", "31.03.2026"), "struct\\[0\\].d"),
    (lambda d: d["comp"][0].__setitem__("t", "100"), "comp\\[0\\].t"),
    (lambda d: d["comp"][0].__setitem__("t", float("nan")), "comp\\[0\\].t"),
    (lambda d: d["comp"][0]["r"].__setitem__(1, "1"), "r.value"),
    (lambda d: d["comp"][0]["r"].__setitem__(2, "5"), "r.qty"),
    (lambda d: d["struct"][0]["r"].__setitem__(1, None), "struct\\[0\\].r.share"),
    (lambda d: d["funds"][0].__setitem__("url", "javascript:alert(1)"), "url"),
    (lambda d: d["funds"][0].__setitem__("url", "ftp://x"), "url"),
])
def test_validation_rejects_bad_types(db, mutate, msg):
    bad = _small(db)
    mutate(bad)
    with pytest.raises(ValueError, match=msg):
        imp.validate(bad)


def test_write_refuses_foreign_dir(db, tmp_path):
    (tmp_path / "status.json").write_text("{}")
    with pytest.raises(ValueError, match="manifest.json"):
        imp.write(db, tmp_path)
    assert (tmp_path / "status.json").read_text() == "{}" and not (tmp_path / "meta.json").exists()


def test_write_cleans_only_own_files(db, tmp_path):
    imp.write(db, tmp_path)
    (tmp_path / "comp-09.json").write_text("[]")      # устаревший свой кусок
    (tmp_path / "status.json").write_text("{}")       # чужой файл
    imp.write(db, tmp_path)
    assert not (tmp_path / "comp-09.json").exists() and (tmp_path / "status.json").read_text() == "{}"
    assert not [p for p in tmp_path.parent.iterdir() if p.name.startswith(".npf-tmp-")]


def test_chunk_limit_on_synthetic(db, monkeypatch, tmp_path):
    small = _small(db)
    one = len(imp.dumps(small["comp"][0]).encode())
    monkeypatch.setattr(imp, "MAX_CHUNK", one * 2)
    files = imp.build_files(small)
    chunks = [n for n in files if n.startswith("comp-")]
    assert len(chunks) >= 2 and sum(files[n][1] for n in chunks) == 3
    assert all(len(files[n][0].encode()) <= one * 2 + 2 for n in chunks)
    monkeypatch.setattr(imp, "MAX_FILE", 10)
    with pytest.raises(ValueError, match="лимита"):
        imp.write(small, tmp_path / "never")
    assert not (tmp_path / "never").exists()


def test_extract_db_with_several_scripts():
    html = ('<script>var a = "</div>";</script><script id="other" type="application/json">{"x":0}</script>'
            '<script id="database" type="application/json">{"a":[1,2]}</script><script>var z=1</script>')
    assert imp.extract_db(html) == {"a": [1, 2]}
