#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
