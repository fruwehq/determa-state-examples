.PHONY: install check run start reset

install:
	python -m venv .venv
	.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock.txt
	.venv/bin/python scripts/install_candidate.py
	.venv/bin/python -m pip install --no-build-isolation --no-deps .

check:
	.venv/bin/ruff check .
	.venv/bin/mypy
	.venv/bin/pytest -q

run:
	.venv/bin/order-service

start: install run

reset:
	rm -f var/orders.sqlite3

container-check:
	python scripts/container-e2e.py
