# Determa State examples

Independent, fully working applications that demonstrate meaningful real-world use
of [Determa State](https://github.com/fruwehq/determa-state-spec).

## Example catalog

- [Python FastAPI order service](python-fastapi-order-service/) - a direct-library
  integration with a realistic payment and fulfillment lifecycle, transactional
  SQLite inbox/aggregate/outbox persistence, restart recovery, idempotent effect
  delivery, and lazy definition migration.

The collection is intended to grow across languages, frameworks, and integration
styles:

- **Direct library integrations** embed a released Determa State implementation.
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
