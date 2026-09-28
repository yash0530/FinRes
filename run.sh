#!/usr/bin/env bash
# Placeholder until the app lands (M4+): starts the single FinRes process on 127.0.0.1:8500.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn finres.app:app --host 127.0.0.1 --port 8500
