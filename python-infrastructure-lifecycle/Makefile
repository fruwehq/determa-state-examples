PYTHON ?= python3

.PHONY: test provider reset

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

provider:
	mkdir -p data
	PYTHONPATH=src $(PYTHON) -m infrastructure_lifecycle.provider --database data/provider.sqlite --scope local-infra

reset:
	rm -f data/provider.sqlite data/provider.sqlite-wal data/provider.sqlite-shm
