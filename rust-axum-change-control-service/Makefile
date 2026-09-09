.PHONY: install fmt lint test build check container-check run reset inspect

install:
	cargo fetch --locked

fmt:
	cargo fmt --check

lint:
	cargo clippy --locked --all-targets -- -D warnings

test:
	cargo test --locked

build:
	cargo build --release --locked

check: fmt lint test build

container-check:
	./scripts/container-e2e.sh

run:
	cargo run --locked

reset:
	rm -f data/change-control.sqlite3 data/change-control.sqlite3-shm data/change-control.sqlite3-wal

inspect:
	sqlite3 data/change-control.sqlite3 '.schema determa_execution_checkpoints' 'SELECT root_instance_id, revision, execution_checkpoint_digest FROM determa_execution_checkpoints;'
