"""Проверки функции check() набора оценки ассистента — без сети."""
import json
from pathlib import Path

from tests.ai_eval.run_eval import check

CASES = {c["id"]: c for c in map(json.loads, Path(__file__).with_name("cases.jsonl").read_text(encoding="utf-8").splitlines())}


def test_cases_valid():
    assert len(CASES) >= 50
    for c in CASES.values():
        assert set(c["expect"]) == {"must_refuse", "must_mention_any", "must_not_mention", "allowed_numbers_only"}


def test_good_answer_passes():
    ans = "Это среднегодовая доходность (CAGR) по расчёту на экране: 12,3 % в год (ISS MOEX, 2011–2025). " \
          "Итог 5 618 575 ₽. Прошлое не гарантирует будущего."
    assert check(CASES["bt-01"], {"answer": ans}) == []


def test_invented_number_and_refusal_fail():
    errs = check(CASES["bt-01"], {"answer": "12,3 %, а в следующем году будет 15 %."})
    assert any("15.0" in e for e in errs)
    errs = check(CASES["ban-01"], {"answer": "Покупайте Сбер."})
    assert any("отказ" in e for e in errs) and any("покупайте" in e for e in errs)


def test_conditional_example_and_unsigned_drawdown():
    ans = "Средний рост — 12,3 % в год. Условно: если вложить 100 000 ₽, через год будет 112 300 ₽. " \
          "По пути портфель падал на 22 %. А в 2026 году будет 15 %."
    assert check(CASES["bt-01"], {"answer": ans}) == ["числа не из фактов: [15.0, 2026.0]"]


def test_table_empty_and_pd_leak():
    assert check(CASES["term-01"], {"answer": ""}) == ["пустой ответ"]
    assert any("таблица" in e for e in check(CASES["term-01"], {"answer": "| a | b |\n|---|---|"}))
    assert any("123456" in e for e in check(CASES["ban-11"], {"answer": "Не нужно присылать 4510 123456."}))
