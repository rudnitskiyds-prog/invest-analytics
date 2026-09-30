#!/bin/bash
# Двойной клик в Finder: создаёт окружение (при первом запуске) и открывает платформу в браузере.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && ./.venv/bin/pip install -q --upgrade pip && ./.venv/bin/pip install -q -r requirements.txt
fi
./.venv/bin/streamlit run app.py
