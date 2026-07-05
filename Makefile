.PHONY: dev tunnel test lint check

dev:
	scripts/run_local.sh

tunnel:
	scripts/run_tunnel.sh

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check .

check: test lint
