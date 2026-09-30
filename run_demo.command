#!/bin/bash
# Демо-режим без интернета: месячные ряды 2010–2025 из tests/fixtures (бэктест, Марковиц, методика).
cd "$(dirname "$0")"
[ -d .venv ] || (python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt)
IP_OFFLINE=1 ./.venv/bin/streamlit run app.py
