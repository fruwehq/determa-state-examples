# AGENTS.md - determa-state-examples

Guidance for coding agents working in this repository.

## Repository role

This repository contains independent, fully working, real-world applications that
demonstrate meaningful Determa State integrations. It is not the manual or tutorial:
those live in [determa-state-docs](https://github.com/fruwehq/determa-state-docs),
where readers build small projects step by step.

Examples may use different languages and frameworks. Some integrate a Determa State
library directly. Future examples may use a language-neutral process, command-line,
or socket boundary after such an execution interface exists; never claim current
support for an interface that has not been implemented.

## Working rules

- One issue to one branch to one pull request. Squash-merge with linear
  history and resolve every review conversation.
- Assign every pull request to `fruwe`.
- Never add assistant attribution to commits, pull requests, comments, or docs.
- Ask before broad changes. Keep each pull request focused and reviewable.
- Do not place shared application code or a shared dependency manifest at the root.
- Do not add isolated feature snippets. Every example must implement a meaningful
  end-to-end workflow.

## Example contract

Each example is complete within one top-level folder and must not depend at runtime,
build time, or source-code level on repository-root files or sibling examples. Its
folder owns:

- a README with purpose, prerequisites, architecture, and usage;
- dependency manifest and lockfile;
- machine definitions and application code;
- automated tests;
- start, reset, maintenance, and debugging instructions;
- a Dockerfile or local infrastructure configuration when needed.

The repository root contains only the catalog, contribution policy, and CI
orchestration. Root CI may enter each example folder and run its local
commands, but must not provide files or dependencies that the example requires
to work.

## Correctness

Examples are explanatory, not normative. Portable behavior is defined by
[determa-state-spec](https://github.com/fruwehq/determa-state-spec) and decided
executably by
[determa-state-conformance](https://github.com/fruwehq/determa-state-conformance).
Pin released Determa State dependencies and keep each example's documented behavior
consistent with those releases.
