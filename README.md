# Determa State examples

Independent, fully working applications that demonstrate meaningful real-world use
of [Determa State](https://github.com/fruwehq/determa-state-spec).

## Example catalog

- [Embedded Python Cloud Tasks workflow](python-cloud-tasks-workflow/) - an unreleased
  0.3.0 workflow with committed intents, real SDK/protobuf objects inside a native
  handler, declared results, immutable destinations and offline transport tests.
- [No-code MCP document workflow](python-mcp-document-workflow/) - official MCP
  stdio tools, remote public-host execution and an application-managed reference
  connector with durable declared results.

- [Customer-native document worker](python-remote-document-worker/) - remote public
  v1 machine execution with customer-owned native archives, durable local outcomes
  and correlated result events.

- [Docker-hosted expense approval](python-docker-expense-service/) - a public v1
  HTTP client/SQLite host workflow with durable command phases, authenticated
  routing, exact receipt retry and independent client/host persistence.

- [Python FastAPI order service](python-fastapi-order-service/) - a unreleased Determa State 0.3.0
  direct-library
  integration with a realistic payment and fulfillment lifecycle, transactional
  SQLite inbox/aggregate/outbox persistence, restart recovery, idempotent effect
  delivery, and lazy definition migration.
- [Rust Axum change-control service](rust-axum-change-control-service/) - a Determa
  State 0.3.0 candidate `CheckpointHost` integration with durable SQLite checkpoints, stable
  operation replay, optimistic concurrency, auditable deployment and rollback
  intents, restart recovery, and terminal change outcomes.

The collection is intended to grow across languages, frameworks, and integration
styles:

- **Direct library integrations** embed an exact public candidate commit during the approved unreleased 0.3.0 implementation.
- **Language-neutral integrations** will communicate with a separate Determa State
  process after a suitable execution protocol or interface exists.

An execution command-line interface and socket protocol are not currently available,
so this repository does not claim or simulate them.

## Repository boundary

Every example must work from its own folder without runtime, build, or
source-code dependencies on repository-root files or sibling examples. The
root is limited to this catalog, contribution guidance, and CI orchestration.
There is deliberately no shared root dependency manifest.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete acceptance contract.

## Tutorials and manual

The beginner manual and tutorials live separately in
[determa-state-docs](https://github.com/fruwehq/determa-state-docs). Those tutorials
teach Determa State through small projects that readers create step by step, including
every command and edit. This repository instead contains complete applications that
show realistic integration and operation.
