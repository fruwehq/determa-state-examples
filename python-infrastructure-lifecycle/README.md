# Infrastructure lifecycle reconciler — unfinished candidate

This application is being completed for unreleased Determa State 0.3.0 under
[scope issue23](https://github.com/fruwehq/determa-state-examples/issues/23).
The current checkpoint implements the independently running durable fake destination
and its foreground process bridge.
It also includes the `format: 1` desired/observed resource machine and its pure State tests.
The durable State host, authenticated native handler, timer, provider reconciliation
and relocation integrations are still unfinished. Do not use this
checkpoint as a full lifecycle, recovery or publication acceptance certificate.

The destination owns `data/provider.sqlite`, separate from the future host database.
It accepts newline-delimited JSON over its owner-controlled stdin and returns one
JSON response per line. No socket, cloud credentials or real resource is involved.
`--scope` is process configuration; requests cannot choose another authorized scope.
The provider commits the resource and exact operation response atomically. An equal
`(scope_identity, effect_id)` replay returns the first response even after a later
resource update. Changed content is a conflict. Retained operation inspection returns
original request and response; an absent receipt is `unknown`, never host rollback proof.

## Run from this folder

Python3.11 or later and Git are required. No root or sibling files are needed.
`requirements.lock.txt` pins the complete hashed dependency closure. `source-lock.json`
pins the exact public unreleased State commit; setup verifies its repository, clean
checkout, exact head and version before installing it. No release tag or registry
publication is used to satisfy candidate installation.

```sh
python3 -m venv .venv
make PYTHON=.venv/bin/python setup
make PYTHON=.venv/bin/python test
make PYTHON=.venv/bin/python provider
```

At the provider prompt, submit:

```json
{"operation":"ensure_resource","scope_identity":"local-infra","effect_id":"effect-A","resource_id":"server-1","expected_revision":null,"desired":{"present":true,"size":"small"}}
```

The response has `status: "applied"`, resource revision `"0"`, the exact desired value
and an operation digest. Inspect durable acceptance using:

```json
{"operation":"inspect_operation","scope_identity":"local-infra","effect_id":"effect-A"}
```

It returns `status: "committed"` and the retained exact request/response. Replay the
first line to recover its response without another resource update. A new operation
on this resource must supply `expected_revision: "0"`. EOF stops the provider.

## Restart, maintenance and debugging

Stop with EOF and run `make provider` again using the same configured scope/path.
Existing journals and origin evidence are validated; damaged or missing native
records are refused rather than repaired. The resource table must match the complete
immutable operation journal. To inspect offline, stop the process and open
`sqlite3 data/provider.sqlite`; query `provider_operations` or `provider_resources`.
Never edit rows to resolve an ambiguous operation. Read retained native receipts and
reconcile through authorized host operations once that integration is implemented.

`make reset` deletes only this local demonstration's provider database and WAL files;
it destroys its deduplication history. Never reset a destination while its host can
still dispatch outstanding operations. This command does not reset a host.

The tests start a real separate Python provider process. At the staged-write and
committed-before-response cuts they send SIGKILL, reopen the actual SQLite database,
inspect native receipts and verify resource/receipt atomicity and equal replay.
They also test the real stdin/stdout protocol, revision/content conflicts, authorized
scope checks, immutable evidence and corruption refusals. These destination tests do
not establish host transaction fate, authorized retry, or the later full crash matrix.

## Foreground process bridge

`infrastructure_lifecycle.client.ProviderProcess` starts the separately running
destination with a configured scope and database path. Calls are serialized across
the JSONL boundary. Scope authorization happens before transport; response size and
elapsed time are bounded. `inspect(effect_id)` reads the destination's retained
original request/response. `verify_retained(request, response)` independently queries
that evidence and compares both exact values; equal caller-supplied strings alone
are insufficient.

A stopped or disconnected process raises `ProviderTransportError("delivery_ambiguous")`
and closes that bridge. It never retries the request, starts a replacement destination,
or resets deduplication history. A newly opened bridge can inspect the same durable
destination. An `unknown` observation still does not prove that a host transaction
rolled back or authorize a new attempt. These APIs are application-owned preparation
for the pending verified State handler, not a completed native-effect profile.

The bridge tests call a real child process, restart against retained native evidence,
reject changed requests and unauthorized scopes, and exercise SIGKILL and SIGSTOP
followed by a transport timeout. The host admission/recovery decisions remain pending.

## Desired and observed resource model

`src/infrastructure_lifecycle/machine.yaml` models one `server-1` resource. Desired
presence/size, observed presence/size/revision, and the original outstanding request
are separate variables. `set_desired` changes only desired state. Explicit `reconcile`
emits an external request and pins that desired snapshot and business operation token.
Later desired changes cannot rewrite an outstanding provider request. A matching
`resource_applied` updates observed state from the original request, leaving any newer
desired state for the next explicit reconciliation. Stale tokens or changed original
request values cannot update observed state.

An unknown provider result retains pending work in `uncertain`; another `reconcile`
cannot start work there. Native acceptance evidence must be reconciled by the pending
host integration. Domain rejection preserves observed state and needs explicit new
work. A converged known resource emits nothing. The model tests exercise actual State
creation, admission and stepping, including desired changes during work, stale results,
uncertainty, removal and convergence. They do not persist a host or dispatch its emitted intent.

Business-token checks express workflow consistency; they do not authenticate a worker,
scope or provider. The future durable host must authenticate and verify native evidence
before admitting these inputs. Full host-owned checkpoint/journal transactions,
outcome/admission recovery, timers and relocation remain acceptance requirements.
