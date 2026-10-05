.PHONY: install check host start reset inspect
install:
	python -m venv .venv
	.venv/bin/python -m pip install --require-hashes -r requirements.lock.txt
	.venv/bin/python scripts/install_candidate.py
check:
	.venv/bin/ruff check .
	.venv/bin/pytest -q
host:
	.venv/bin/python server.py
start:
	docker compose up --build -d
inspect:
	.venv/bin/python client.py read ticket-42
reset:
	docker compose down -v
	rm -f var/*.sqlite3 var/*.sqlite3-shm var/*.sqlite3-wal

container-check:
	.venv/bin/python scripts/container-e2e.py
