# Infrastructure lifecycle reconciler — unfinished candidate

This application is being completed for unreleased Determa State 0.3.0 under
[scope issue23](https://github.com/fruwehq/determa-state-examples/issues/23).
The current checkpoint implements the independently running durable fake destination.
The Determa host, desired/observed machine, authenticated native handler, timer,
reconciliation and relocation integrations are still unfinished. Do not use this
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

Python3.11 or later and its standard library are sufficient for this provider checkpoint.
No root or sibling files are needed. Full application source/dependency locks will be
added with the reviewed engine integration.

```sh
make test
make provider
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
