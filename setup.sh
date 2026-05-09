#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "Project environment is ready. Set OPENROUTER_API_KEY in .env before live runs."

