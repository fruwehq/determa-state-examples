# Determa State examples

Independent, complete applications using the approved **unreleased 0.3.0 candidate**
of [Determa State](https://github.com/fruwehq/determa-state-spec). Each folder owns
its source, machine definitions, exact public engine pin, dependencies, lockfiles,
tests and operation instructions. No package release or available managed service
is assumed.

## Example catalog

- [Embedded Cloud Tasks workflow](python-cloud-tasks-workflow/) — an embedded
  library workflow with committed intents, real Google SDK/protobuf objects inside
  an application-owned handler, immutable queue destinations and offline tests.
- [FastAPI order service](python-fastapi-order-service/) — application-owned
  transactional SQLite inbox/aggregate/outbox coordination, payment and fulfillment
  outcomes, restart recovery, effect delivery and definition migration.
- [Axum change-control service](rust-axum-change-control-service/) — Rust
  `CheckpointHost` with SQLite persistence, optimistic concurrency, stable operation
  replay, auditable deployment/rollback intents and terminal change outcomes.
- [Docker-hosted expense approval](python-docker-expense-service/) — an
  authenticated public-v1 HTTP host and client, durable complete command phases,
  exact receipt retry and independent client/host persistence.
- [Customer-native document worker](python-remote-document-worker/) — remote
  machine execution with customer-owned native ZIP archives, saved local outcomes
  and correlated declared result events.
- [No-code MCP document workflow](python-mcp-document-workflow/) — official MCP
  stdio tools invoke remote workflows and an application-managed reference connector
  without requiring the tool consumer to write integration code.
- [AI support triage through MCP](python-ai-mcp-triage/) — a remote workflow invokes
  an MCP model tool with native OpenAI SDK objects and durably reports declared
  results; an explicit offline provider fixture is the default.

These applications cover the six approved integration categories: embedded native
SDK work, application-owned persistence, remote Docker execution, customer-native
workers, no-code/reference connectors, and AI through MCP. FastAPI and Axum provide
independent Python and Rust examples of application-owned hosting.

Direct library integrations embed the candidate engines. Remote integrations use
the implemented version-1 public host/client contract through application-owned
HTTP adapters. The engines remain pure libraries; these adapters are reference
applications, with no claim of a generic engine execution CLI, socket server or
private hosted control plane. Read each folder's capability and recovery limits.
Application-owned connector/worker examples do not advertise verified §19 helper
profiles or distributed exactly-once guarantees.

## Candidate source closure

The applications pin the merged public candidate engines exactly:

| Engine | Candidate | Public source commit |
|---|---|---|
| Python | 0.3.0 | `61fd77ed2e73365e80739d6d99c50c54fc6f0da6` |
| Rust | 0.3.0 | `272e37e7255ae4c650f714a94ea6cddea61d245d` |

Each folder records its own pin in `source-lock.json` and its manifest. Python
folders own hashed third-party locks and candidate installers; the Rust folder owns
its Cargo lock. Machine YAML stays `format: 1` and portable artifacts stay version
1. Root CI checks the candidate catalog with:

```sh
python scripts/check-release-freshness.py --candidate-version 0.3.0
```

Run each folder's checks independently; the root does not supply runtime source or
dependencies. Container checks use disposable local data. The AI check explicitly
forces its offline provider even when a developer's shell is configured for live
model calls. No tag, package publication or website deployment is part of this
candidate implementation. Publication requires separate approval after audit.

## Repository boundary

Every example works from its own folder without runtime, build or source-code
requirements on repository-root files or sibling examples. The root contains only
the catalog, contribution guidance and CI orchestration. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the complete acceptance contract.

## Tutorials and manual

The [manual and tutorials](https://github.com/fruwehq/determa-state-docs) teach the
candidate contracts through small projects built step by step. This repository
contains complete applications demonstrating integration and operation.
