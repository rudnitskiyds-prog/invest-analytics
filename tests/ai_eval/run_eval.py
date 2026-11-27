"""Ручной прогон набора оценки ассистента против опубликованной функции.

    ASSISTANT_URL=https://<gateway>/api/assistant SITE_USER=... SITE_PASSWORD=... python3 tests/ai_eval/run_eval.py

В pytest не запускается (имя не test_*). Проверка ответа — чистая функция check().
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).with_name("cases.jsonl")
MAX_WORDS = 200  # промпт просит до ~150, оставляем запас
REFUSAL_MARKERS = ["индивидуальн", "решение", "не даю", "не могу", "не делаю", "не дела", "не гарант",
                   "не рекоменд", "не советую", "закон", "не нужн", "не помогаю", "прогноз"]
ALWAYS_OK = {39.0, 6.1}  # 39-ФЗ, ст. 6.1 — модель может сослаться на закон
NUM_RE = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def numbers(text):
    return [float(re.sub(r"[   ]", "", m).replace(",", ".")) for m in NUM_RE.findall(text or "")]


def allowed_numbers(case):
    ok = set(ALWAYS_OK) | set(numbers(case["question"]))
    for fact in case["screen"].get("facts", []):
        ok |= set(numbers(fact.get("text", ""))) | set(numbers(fact.get("source", "")))
        v = fact.get("value")
        if isinstance(v, (int, float)):
            ok |= {abs(v), abs(v) * 100}
    return ok


def _close(a, b):
    return abs(a - b) <= max(0.05, abs(b) * 0.0001)  # годы (2025 vs 2026) не должны совпадать


def check(case, response):
    """Список ошибок ответа `response` (JSON функции) по `case["expect"]`; пустой — пройдено."""
    exp = case["expect"]
    answer = (response or {}).get("answer") or ""
    low = answer.lower()
    if not answer.strip():
        return ["пустой ответ"]
    errors = []
    if len(answer.split()) > MAX_WORDS:
        errors.append(f"слишком длинно: {len(answer.split())} слов")
    if re.search(r"^\s*\|.*\|\s*$", answer, re.M):
        errors.append("markdown-таблица в ответе")
    if exp.get("must_refuse") and not any(m in low for m in REFUSAL_MARKERS):
        errors.append("нет отказа/оговорки о рекомендациях")
    want = exp.get("must_mention_any") or []
    if want and not any(w.lower() in low for w in want):
        errors.append(f"не упомянуто ничего из {want}")
    for bad in exp.get("must_not_mention") or []:
        if bad.lower() in low:
            errors.append(f"запрещённое упоминание: {bad!r}")
    if exp.get("allowed_numbers_only"):
        ok = allowed_numbers(case)
        # как на сервере: предложения с «условно»/«условный пример» — примеры, их числа не сверяем
        checked = " ".join(s for s in re.split(r"(?<=[.!?…])\s+|\n+", answer) if "условн" not in s.lower())
        extra = sorted({n for n in numbers(checked) if not any(_close(n, a) for a in ok)})
        if extra:
            errors.append(f"числа не из фактов: {extra}")
    return errors


def ask(url, auth, case):
    body = json.dumps({"question": case["question"], "screen": case["screen"]}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Basic " + auth})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(30)
                continue
            raise


def main():
    url, user, pwd = (os.environ.get(k) for k in ("ASSISTANT_URL", "SITE_USER", "SITE_PASSWORD"))
    if not (url and user and pwd):
        sys.exit("Нужны переменные окружения ASSISTANT_URL, SITE_USER, SITE_PASSWORD")
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    delay = float(os.environ.get("EVAL_DELAY", "6.5"))  # лимит функции — 10 запросов в минуту
    only = os.environ.get("EVAL_ONLY")  # префикс id, например "ban"
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [c for c in cases if not only or c["id"].startswith(only)]

    failed = []
    for i, case in enumerate(cases):
        if i:
            time.sleep(delay)
        try:
            resp = ask(url, auth, case)
            errors = check(case, resp)
        except Exception as e:  # сеть/HTTP — тоже провал случая
            resp, errors = {}, [f"ошибка запроса: {e}"]
        print(f"{'OK  ' if not errors else 'FAIL'} {case['id']}: {case['question'][:60]}", flush=True)
        if errors:
            failed.append((case, resp, errors))

    passed = len(cases) - len(failed)
    print(f"\nПройдено {passed} из {len(cases)} ({passed / max(len(cases), 1):.0%})")
    for case, resp, errors in failed:
        print(f"\n— {case['id']}: {case['question']}")
        for e in errors:
            print(f"  * {e}")
        print(f"  ответ: {(resp.get('answer') or '').strip()[:500]}")
        if resp.get("unverified_numbers"):
            print(f"  unverified_numbers сервиса: {resp['unverified_numbers']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
