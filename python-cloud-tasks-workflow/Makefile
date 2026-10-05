.PHONY: install check start inspect reset
install:
	python -m venv .venv
	.venv/bin/python -m pip install --require-hashes -r requirements.lock.txt
	.venv/bin/python scripts/install_candidate.py
check:
	.venv/bin/ruff check .
	.venv/bin/pytest -q
start:
	.venv/bin/python app.py demo
inspect:
	.venv/bin/python app.py inspect
reset:
	rm -f var/workflow.sqlite3 var/workflow.sqlite3-shm var/workflow.sqlite3-wal
