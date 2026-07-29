# Python FastAPI order service

A complete order workflow application that embeds the released
`determa-state==0.1.0` Python library directly. It exposes an HTTP API, stores portable
Determa aggregates in SQLite, and treats payment and fulfillment calls as durable
host-managed effects.

This is an operating example, not a tutorial. Its code, definitions, dependencies,
database schema, tests, and container configuration are all contained in this folder.
It does not read anything from the repository root or another example.

## What it demonstrates

- payment acceptance and rejection;
- fulfillment request, start, success, and failure;
- cancellation that waits for an external confirmation;
- one SQLite transaction for inbox deduplication, order data, aggregate bytes,
  migration audit, and outbox intents;
- canonical aggregate restoration after process restart;
- idempotent effect recording by Determa `effect_id`;
- a lazy definition upgrade from machine version 1 to 2;
- an explicit active-state remap from `fulfilling` to `shipping`;
- inspection of accepted input, output intents, migration records, and aggregate
  identity.

The local effect adapter records that it attempted delivery. It does not call a payment
provider or warehouse, and delivery does not advance the machine. Remote success or
failure becomes order state only when a later input event reaches the API. This example
does not claim distributed exactly-once delivery or distributed ACID.

## Architecture

```text
HTTP command
   |
   v
FastAPI -> BEGIN IMMEDIATE
             |-- inbox idempotency record
             |-- restore/migrate/dispatch Determa aggregate
             |-- business order projection
             |-- canonical aggregate bytes
             |-- ordered outbox intents
             `-- COMMIT

Outbox adapter -> effect_deliveries(effect_id) -> external system in a real host
External outcome -> later HTTP input event -> next SQLite transaction
```

The Determa core is a pure foreground transform. `OrderService` owns the host
transaction and persists every output intent before any adapter can deliver it.
`effect_id` is the adapter's idempotency key. SQLite protects local data atomically;
external systems still need their own idempotency and retry policies.

The four business-terminal states are inert simple leaves rather than Determa `final`
states. Format 1 completion disposes the root variable scope; keeping these leaves
active preserves the machine-owned `order_status` in the portable aggregate for
durable inspection. Their lack of handlers makes later workflow events invalid.

## Prerequisites

- Python 3.11 through 3.14
- `make`
- optional: Docker with Compose

All direct dependencies are exact pins in `pyproject.toml`. Complete transitive
dependencies and hashes are committed in `requirements.lock.txt` and
`requirements-dev.lock.txt`.

## Start locally

From this folder:

```sh
make start
```

This creates `.venv`, installs only the hashed lockfile, installs this application
without resolving other packages, initializes `var/orders.sqlite3`, and serves
`http://127.0.0.1:8000`. Interactive OpenAPI documentation is available at
`http://127.0.0.1:8000/docs`.

With Docker:

```sh
docker compose up --build
```

The Compose service persists SQLite under this folder's `var/` directory.

## Happy path

Create an order. Save the returned `order_id` for subsequent commands.

```sh
curl -sS -X POST http://127.0.0.1:8000/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: create-order-1001' \
  -d '{"customer_id":"customer-42","amount_cents":12900}'
```

Inspect the pending `payment_requested` output intent:

```sh
curl -sS 'http://127.0.0.1:8000/admin/outbox'
```

Record delivery through the replaceable local adapter:

```sh
curl -sS -X POST http://127.0.0.1:8000/admin/outbox/deliver
```

Delivery alone leaves the order in `awaiting_payment`. Supply the external outcome as a
new input, then progress fulfillment:

```sh
ORDER_ID='<order_id from create>'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$ORDER_ID/events/payment_accepted" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: payment-accepted-1001' \
  -d '{}'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$ORDER_ID/events/fulfillment_started" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fulfillment-started-1001' \
  -d '{}'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$ORDER_ID/events/fulfillment_succeeded" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fulfillment-succeeded-1001' \
  -d '{}'
```

The final state is `completed`.

## Rejection and failure

Reject payment before it is accepted:

```sh
REJECTED_ORDER_ID=$(
  curl -sS -X POST http://127.0.0.1:8000/orders \
    -H 'Content-Type: application/json' \
    -H 'Idempotency-Key: create-rejected-order-1001' \
    -d '{"customer_id":"customer-rejected","amount_cents":4900}' |
    python -c 'import json,sys; print(json.load(sys.stdin)["order_id"])'
)

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$REJECTED_ORDER_ID/events/payment_rejected" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: payment-rejected-1001' \
  -d '{"reason":"card declined"}'
```

After payment acceptance, a warehouse failure instead uses:

```sh
FAILED_ORDER_ID=$(
  curl -sS -X POST http://127.0.0.1:8000/orders \
    -H 'Content-Type: application/json' \
    -H 'Idempotency-Key: create-failed-order-1001' \
    -d '{"customer_id":"customer-failed","amount_cents":8900}' |
    python -c 'import json,sys; print(json.load(sys.stdin)["order_id"])'
)

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$FAILED_ORDER_ID/events/payment_accepted" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: payment-accepted-failed-order-1001' \
  -d '{}'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$FAILED_ORDER_ID/events/fulfillment_failed" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fulfillment-failed-1001' \
  -d '{"reason":"warehouse unavailable"}'
```

These lead to `payment_rejected` and `failed`. Sending an event that is invalid for the
current state returns HTTP 409; the transaction rolls back and does not accept the
idempotency key.

## Cancellation

After payment acceptance, submit `cancel_requested`. The machine enters
`cancellation_pending` and emits `fulfillment_cancellation_requested`. It becomes
`cancelled` only after a separate `fulfillment_cancelled` input. A
`fulfillment_cancellation_failed` input moves it to `failed`.

```sh
CANCELLED_ORDER_ID=$(
  curl -sS -X POST http://127.0.0.1:8000/orders \
    -H 'Content-Type: application/json' \
    -H 'Idempotency-Key: create-cancelled-order-1001' \
    -d '{"customer_id":"customer-cancelled","amount_cents":6900}' |
    python -c 'import json,sys; print(json.load(sys.stdin)["order_id"])'
)

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$CANCELLED_ORDER_ID/events/payment_accepted" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: payment-accepted-cancelled-order-1001' \
  -d '{}'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$CANCELLED_ORDER_ID/events/cancel_requested" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: cancel-requested-1001' \
  -d '{}'

curl -sS -X POST \
  "http://127.0.0.1:8000/orders/$CANCELLED_ORDER_ID/events/fulfillment_cancelled" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fulfillment-cancelled-1001' \
  -d '{}'
```

## Duplicate delivery

Repeat any request with the same `Idempotency-Key` and identical order, event, and
payload. The stored original response is returned with `"duplicate": true`; Determa is
not dispatched again and no outbox intent is repeated.

Reusing a key for different content returns HTTP 409. The durable inbox is deliberately
host-owned rather than an unbounded deduplication history inside the aggregate.

Calling `/admin/outbox/deliver` repeatedly increments the attempt count but creates only
one `effect_deliveries` row per `effect_id`. In a production adapter, send the same
effect ID to the remote system and require that system to apply an equivalent
idempotency rule.

## Restart

Stop the process with Control-C and run `make run` again. The API reads the same SQLite
file. `GET /orders/{order_id}` verifies and restores the canonical aggregate using its
exact trusted definition fingerprint; no in-memory engine session is required.

For Docker, `docker compose down` followed by `docker compose up` uses the same
folder-local `var/orders.sqlite3`.

## Definition migration

`machines/order-v1.yaml` calls the active fulfillment state `fulfilling`.
`machines/order-v2.yaml` renames it to `shipping`. The committed trusted descriptor
`migrations/order-v1-to-v2.json` maps every active state and counter explicitly,
including:

```text
/machines/0/root/states/fulfilling
  -> /machines/0/root/states/shipping
```

To reproduce a deployment upgrade without rewriting every row:

1. Start with `ORDER_DEFINITION_VERSION=1 ORDER_DATABASE=var/migration.sqlite3 make run`.
2. Create an order, accept payment, and submit `fulfillment_started`.
3. Stop the process.
4. Restart with `ORDER_DEFINITION_VERSION=2 ORDER_DATABASE=var/migration.sqlite3 make run`.
5. Read the order: reads remain on trusted version 1 and do not rewrite it.
6. Submit its next valid command. `migrate_and_dispatch` applies the trusted migration
   and the command in the same transaction.

The maintenance endpoint can migrate without dispatching an event:

```sh
curl -sS -X POST "http://127.0.0.1:8000/admin/orders/$ORDER_ID/migrate"
```

This uses `migrate_aggregate(..., maintenance_mode=True)`, persists the audit record,
and is a no-op after the row reaches the configured version. A missing state mapping,
untrusted definition, invalid descriptor, or failed migration aborts the transaction.
The service never interprets old aggregate bytes with the new YAML directly.

## Inspection and debugging

```sh
curl -sS "http://127.0.0.1:8000/orders/$ORDER_ID"
curl -sS "http://127.0.0.1:8000/admin/outbox?order_id=$ORDER_ID"
curl -sS "http://127.0.0.1:8000/admin/orders/$ORDER_ID/debug"
sqlite3 var/orders.sqlite3 '.tables'
sqlite3 var/orders.sqlite3 \
  'select event_id,event_name,accepted_at from inbox order by accepted_at;'
sqlite3 var/orders.sqlite3 \
  'select effect_id,event_name,delivery_status,delivery_attempts from outbox;'
```

The debug endpoint includes the active state, machine version, validated definition
fingerprint, canonical aggregate digest, accepted inbox events, ordered outbox rows,
and migration audits. It intentionally does not expose mutable engine internals.

## Reset

Stop the service, then:

```sh
make reset
```

For the migration walkthrough, remove its separate database explicitly:

```sh
rm -f var/migration.sqlite3
```

## Tests and quality gates

```sh
make install
make check
```

The tests cover the happy path, payment rejection, fulfillment failure, cancellation,
duplicate commands, idempotent effect delivery, transaction rollback, restart,
automatic migration-and-dispatch, explicit maintenance migration, and the exact state
rename.

## Maintenance

When changing dependencies, update exact pins in `pyproject.toml`,
`requirements.in`, and `requirements-dev.in`, then rebuild both hashed lockfiles in a
temporary maintenance environment:

```sh
python -m venv .lock-venv
.lock-venv/bin/python -m pip install 'pip==24.3.1' 'pip-tools==7.5.0'
.lock-venv/bin/pip-compile --generate-hashes \
  --output-file=requirements.lock.txt requirements.in
.lock-venv/bin/pip-compile --generate-hashes \
  --output-file=requirements-dev.lock.txt requirements-dev.in
rm -rf .lock-venv
make install
make check
```

Definition changes require a new immutable machine file and, when aggregate shape
changes, a complete trusted migration descriptor. Keep old definitions available for
restoration until every stored aggregate has migrated or has been retired. Never edit a
published definition in place.

## Limitations

- SQLite serializes writes and is suitable for this local example, not every production
  workload.
- The adapter only records delivery locally; it does not integrate with a real payment
  or fulfillment provider.
- There is no broker acknowledgement loop, scheduler, timer host, authentication, or
  multi-tenant authorization.
- Exactly-once behavior is not claimed across SQLite and remote systems.
- The example keeps two trusted definitions locally. A production service would
  normally use an immutable artifact registry and explicit trust policy.
