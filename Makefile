.PHONY: install lint test setup-data run-sample serve browser-smoke

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

lint:
	. .venv/bin/activate && ruff check .

test:
	. .venv/bin/activate && pytest

setup-data:
	. .venv/bin/activate && trust-analytics setup

run-sample:
	. .venv/bin/activate && trust-analytics run --review-mode demo-approve

serve:
	. .venv/bin/activate && uvicorn trust_analytics.api:app --host 0.0.0.0 --port 8080

browser-smoke:
	.venv/bin/python scripts/browser_smoke.py --url http://127.0.0.1:8080/
