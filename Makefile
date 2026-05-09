.PHONY: install lint test run-sample

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

lint:
	. .venv/bin/activate && ruff check .

test:
	. .venv/bin/activate && pytest

run-sample:
	. .venv/bin/activate && pluang-agent run

