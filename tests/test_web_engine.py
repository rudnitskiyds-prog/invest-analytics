"""Тесты JS-движка web/lib (tests/web/*.test.mjs, node:test) — запуск из pytest (инвариант 1)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_web_engine_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден в PATH: тесты JS-движка (tests/web) пропущены")
    # Node 22 трактует аргумент --test как glob/файл; каталог целиком он не принимает
    proc = subprocess.run(
        [node, "--test", "--test-reporter=spec", "tests/web/*.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"node --test завершился с кодом {proc.returncode}\n"
                    f"{proc.stdout[-20000:]}\n{proc.stderr[-5000:]}", pytrace=False)
