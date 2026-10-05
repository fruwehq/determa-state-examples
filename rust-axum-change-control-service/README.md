# Rust Axum change-control service

A complete deployment change-control API built with Rust, Axum, Determa State
the unreleased 0.3.0 candidate and its version-1 SQLite `CheckpointHost`.

The workflow is operational rather than illustrative: a requester submits a change, a
reviewer starts review and approves or rejects it, and an operator reports deployment
and rollback outcomes. Outcome states are application-terminal: the HTTP boundary
refuses later commands while preserving the final configuration for inspection.
Approval creates a durable `deployment_requested` outbox intent.
A failed deployment creates a durable `rollback_requested` intent. The application
does not pretend to deliver either intent automatically.

## Architecture

- `machines/change-control.yaml` owns the portable workflow.
- Axum owns HTTP, request validation, and the example's `x-actor-id` / `x-actor-role`
  authorization policy. These identities are application data, not Determa identity
  semantics.
- Rust's `CheckpointHost` creates version-1 checkpoints and admits/processes normalized delivery envelopes.
- `SqliteExecutionStore` durably stores aggregate state, retained delivery receipts,
  ready/deferred queues, and outbox records with revision/digest compare-and-swap.
- Every mutating request supplies a stable operation ID and the last observed revision
  plus checkpoint digest. Exact retries replay; stale revisions and changed reuse of an
  operation ID fail.
- No socket protocol, remote Determa API, effect worker, scheduler, timer service, or
  automatic retry loop is implemented here.

This differs from the sibling FastAPI example: FastAPI calls the pure core inside an
application-owned SQLite transaction. This service uses Determa State's synchronous
checkpoint host and its execution-store contract directly.

## Run locally

Prerequisites: Rust 1.86 or newer, Cargo, and SQLite's command-line tool only if you
want to run the raw inspection command.

```sh
cargo run --locked
```

The service listens on `http://127.0.0.1:8080` and stores data in
`data/change-control.sqlite3`. Override these with `CHANGE_CONTROL_ADDRESS` and an
absolute or working-directory-relative `CHANGE_CONTROL_DATABASE` path.

```sh
curl http://127.0.0.1:8080/health
```

Create a change:

```sh
curl -sS -X POST http://127.0.0.1:8080/changes \
  -H 'content-type: application/json' \
  -H 'x-actor-id: alice' -H 'x-actor-role: requester' \
  -d '{"change_id":"chg-100","creation_id":"create-chg-100","title":"Rotate payment signing key"}'
```

Read the returned `revision` and `checkpoint_digest`, then start review:

```sh
curl -sS -X POST http://127.0.0.1:8080/changes/chg-100/review \
  -H 'content-type: application/json' \
  -H 'x-actor-id: bob' -H 'x-actor-role: reviewer' \
  -d '{"operation_id":"review-chg-100","expected_revision":"0","expected_checkpoint_digest":"REPLACE_FROM_CREATE"}'
```

Each response returns the next guard. The remaining endpoints are:

```text
POST /changes/{id}/approve
POST /changes/{id}/reject
POST /changes/{id}/deployment/succeeded
POST /changes/{id}/deployment/failed
POST /changes/{id}/rollback/succeeded
POST /changes/{id}/rollback/failed
GET  /changes/{id}
GET  /changes/{id}/outbox
```

Decision and outcome bodies use the same operation and guard fields. Rejection,
deployment failure, and rollback failure add `reason`; deployment success adds
`deployment_id`. Deployment and rollback result events are correlated with the
change ID.

## Validate

Everything needed by the example is in this folder:

```sh
make install
make check
```

`make check` runs formatting, Clippy with warnings denied, locked integration tests,
and a locked release build. The HTTP tests cover approval, rejection, deployment,
rollback, exact replay, changed-ID reuse, stale revision conflicts, authorization,
restart recovery, terminal behavior, and durable outbox inspection.

## Container

```sh
docker compose up --build
curl http://127.0.0.1:8080/health
docker compose down
```

The named volume preserves checkpoints across container replacement. The image runs as
a non-root user and includes a health check.

Run the complete container health, approval, outbox, and restart scenario with:

```sh
make container-check
```

## Operations

Inspect through the supported API:

```sh
curl -sS http://127.0.0.1:8080/changes/chg-100
curl -sS http://127.0.0.1:8080/changes/chg-100/outbox
```

For local debugging only, inspect checkpoint metadata without editing it:

```sh
make inspect
RUST_LOG=debug cargo run --locked
```

The SQLite execution-store schema deliberately prevents checkpoint deletion. To reset
this disposable example environment, stop the process and run:

```sh
make reset
```

Do not delete or edit rows in a live store. Definition migration, backup/relocation,
effect delivery workers, timers, and remote hosting require their own qualified
contracts and are intentionally outside this example.

This candidate uses the exact public Git commit in `Cargo.toml`, `Cargo.lock`, and
`source-lock.json`; no 0.3.0 release or tag is assumed. Use a fresh disposable database
when upgrading this example from 0.2.0. Machine definitions retain numeric `format: 1`.
