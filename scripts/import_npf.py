"""Импорт базы «Монитор портфелей НПФ» из присланного HTML в public/data/npf/ (кусками ≤ ~3 МБ).

    python -m scripts.import_npf <путь к html> [--out public/data/npf]

Исходный HTML в репозиторий не кладём. Вывод детерминированный (минифицированный JSON, фиксированный
порядок ключей и кусков). Формат файлов — web/CONTRACT.md, раздел «Пенсионная индустрия».
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "public" / "data" / "npf"
MAX_CHUNK = 2_500_000          # байт на кусок comp (лимит шлюза страхуем с запасом: ≤ ~3 МБ)
MAX_FILE = 3_000_000           # жёсткий лимит любого файла
KEYS = ["built", "monitorUpdated", "classes", "sectors", "funds", "names", "isins", "issuers", "pages", "inst",
        "groups", "srcs", "notes", "labels", "issues", "comp", "struct", "sectorNote", "aumNote"]
META_KEYS = ["built", "monitorUpdated", "classes", "sectors", "funds", "labels", "issues", "sectorNote", "aumNote",
             "isins", "issuers", "groups", "inst"]
DICT_FILES = {"names": "names.json", "pages": "pages.json", "srcs": "srcs.json", "notes": "notes.json"}
OWN_FILES = re.compile(r"manifest\.json|(meta|names|pages|srcs|notes|struct)\.json|comp-\d+\.json")  # только эти файлы мы пишем/чистим
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
URL_RE = re.compile(r"https?://\S+", re.I)


def extract_db(html: str) -> dict:
    m = re.search(r'<script id="database" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError('в HTML нет <script id="database" type="application/json">')
    return json.loads(m.group(1))


def _ix(v, n: int, what: str):
    """Индекс в словарь: целое 0 ≤ v < n (None допускается там, где вызывающий это разрешил)."""
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < n:
        raise ValueError(f"{what}: индекс {v!r} вне словаря длиной {n}")


def _num(v, what: str, none_ok: bool = False):
    """Конечное число (не bool, не NaN/inf); None — только если none_ok."""
    if v is None and none_ok:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what}: ожидалось число, получено {v!r}")


def _date(v, what: str):
    if not isinstance(v, str) or not DATE_RE.fullmatch(v):
        raise ValueError(f"{what}: дата {v!r} не в формате YYYY-MM-DD")


def validate(db: dict) -> None:
    miss = [k for k in KEYS if k not in db]
    if miss:
        raise ValueError(f"нет ключей: {miss}")
    L = {k: len(db[k]) for k in ("classes", "sectors", "funds", "names", "isins", "issuers", "pages", "inst",
                                  "groups", "srcs", "notes", "labels", "issues")}
    if L["classes"] != 17:
        raise ValueError(f"classes: ожидалось 17, получено {L['classes']}")
    for i, it in enumerate(db["inst"]):
        if len(it) != 6:
            raise ValueError(f"inst[{i}]: ожидалось 6 полей")
        for v, d in zip(it, ("names", "isins", "issuers", "classes", "sectors", "groups")):
            _ix(v, L[d], f"inst[{i}]")
    for i, c in enumerate(db["comp"]):
        _ix(c["f"], L["funds"], f"comp[{i}].f")
        _date(c.get("d"), f"comp[{i}].d")
        _num(c.get("t"), f"comp[{i}].t")
        for key in ("nav", "li", "rep"):
            _num(c.get(key), f"comp[{i}].{key}", none_ok=True)
        for key in ("c", "s"):
            for kk, vv in c[key].items():
                _num(vv, f"comp[{i}].{key}[{kk}]")
        if c["k"] not in (0, 1):
            raise ValueError(f"comp[{i}].k={c['k']!r}")
        _ix(c["src"], L["srcs"], f"comp[{i}].src")
        _ix(c["nl"], L["notes"], f"comp[{i}].nl")
        for n in c["n"]:
            _ix(n, L["notes"], f"comp[{i}].n")
        r = c["r"]
        if len(r) % 4:
            raise ValueError(f"comp[{i}].r: длина не кратна 4")
        for j in range(0, len(r), 4):
            _ix(r[j], L["inst"], f"comp[{i}].r.inst")
            _ix(r[j + 3], L["pages"], f"comp[{i}].r.page")
            _num(r[j + 1], f"comp[{i}].r.value")
            _num(r[j + 2], f"comp[{i}].r.qty", none_ok=True)
        for k in c["c"]:
            _ix(int(k), 17, f"comp[{i}].c")
        for k in c["s"]:
            _ix(int(k), L["sectors"], f"comp[{i}].s")
    for i, s in enumerate(db["struct"]):
        _ix(s["f"], L["funds"], f"struct[{i}].f")
        _date(s.get("d"), f"struct[{i}].d")
        if s.get("k") not in (0, 1):
            raise ValueError(f"struct[{i}].k={s.get('k')!r}")
        _num(s.get("sum"), f"struct[{i}].sum", none_ok=True)
        _ix(s["src"], L["srcs"], f"struct[{i}].src")
        if s.get("nt") is not None:
            _ix(s["nt"], L["notes"], f"struct[{i}].nt")
        for j in range(0, len(s["r"]), 2):
            _ix(s["r"][j], 17, f"struct[{i}].r")
            _num(s["r"][j + 1], f"struct[{i}].r.share")
        sr = s["sr"]
        if len(sr) % 3:
            raise ValueError(f"struct[{i}].sr: длина не кратна 3")
        for j in range(0, len(sr), 3):
            _ix(sr[j], L["labels"], f"struct[{i}].sr.label")
            _num(sr[j + 1], f"struct[{i}].sr.share")
            if sr[j + 2] != -1:
                _ix(sr[j + 2], 17, f"struct[{i}].sr.class")
        for n in s.get("is", []):
            _ix(n, L["issues"], f"struct[{i}].is")
    for f in db["funds"]:
        if "id" not in f or "name" not in f:
            raise ValueError("funds: нет id/name")
        if f.get("url") and not URL_RE.fullmatch(f["url"]):
            raise ValueError(f"funds[{f['id']}].url: допустимы только http(s)://, получено {f['url']!r}")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_files(db: dict) -> dict[str, tuple[str, int]]:
    """{имя файла: (текст, число записей)} без manifest.json."""
    files = {"meta.json": (dumps({k: db[k] for k in META_KEYS}), len(db["funds"]))}
    for k, name in DICT_FILES.items():
        files[name] = (dumps(db[k]), len(db[k]))
    files["struct.json"] = (dumps(db["struct"]), len(db["struct"]))
    chunks, cur, size = [], [], 2
    for c in db["comp"]:           # порядок исходной базы сохраняется; куски — подряд идущие записи
        s = len(dumps(c).encode()) + 1
        if cur and size + s > MAX_CHUNK:
            chunks.append(cur)
            cur, size = [], 2
        cur.append(c)
        size += s
    chunks.append(cur)
    for n, ch in enumerate(chunks, 1):
        files[f"comp-{n:02d}.json"] = (dumps(ch), len(ch))
    return files


def _check_out(out: Path) -> None:
    """Пишем только в свою папку: пустую/новую или уже содержащую manifest.json."""
    if out.exists() and not out.is_dir():
        raise ValueError(f"{out}: не папка")
    if out.is_dir() and any(out.iterdir()) and not (out / "manifest.json").is_file():
        raise ValueError(f"{out}: папка не пуста и в ней нет manifest.json — отказ (чужие файлы не трогаем)")


def write(db: dict, out: Path = OUT_DIR) -> dict:
    validate(db)
    files = build_files(db)
    _check_out(out)
    manifest = {"built": db["built"], "monitorUpdated": db["monitorUpdated"], "files": []}
    blobs = {}
    for name, (text, n) in files.items():
        data = text.encode()
        if len(data) > MAX_FILE:
            raise ValueError(f"{name}: {len(data)} байт больше лимита {MAX_FILE}")
        blobs[name] = data
        part = ("comp" if name.startswith("comp-") else name[:-5])
        manifest["files"].append({"name": name, "part": part, "bytes": len(data), "records": n,
                                  "sha256": hashlib.sha256(data).hexdigest()})
    blobs["manifest.json"] = dumps(manifest).encode()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".npf-tmp-", dir=out.parent))   # рядом с out → rename в пределах одной ФС
    try:
        for name, data in blobs.items():
            (tmp / name).write_bytes(data)
        for name in blobs:                  # manifest.json последним (dict сохраняет порядок вставки)
            os.replace(tmp / name, out / name)
        for p in out.iterdir():             # устаревшие СВОИ файлы прошлых запусков
            if p.is_file() and OWN_FILES.fullmatch(p.name) and p.name not in blobs:
                p.unlink()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", type=Path)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    m = write(extract_db(a.html.read_text(encoding="utf-8")), a.out)
    for f in m["files"]:
        print(f"{f['name']:14} {f['bytes']:>9} Б  {f['records']:>6} зап.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
