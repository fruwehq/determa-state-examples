# AI support triage through MCP

A complete support workflow using the unreleased Determa State 0.3.0 public v1
client/host. The remote machine commits an `analysis_requested` intent. A customer
worker then invokes an application-owned MCP model tool over real stdio messages.
That tool uses the native OpenAI SDK and returns only declared `category` and
`summary` fields. The worker saves the outcome locally before admitting and
processing the correlated `analysis_ready` event remotely. The machine reaches
`routed` only when that declared result is processed.

The default provider is explicitly an offline fixture using the OpenAI SDK's
mock HTTP transport. It exercises SDK response parsing and actual MCP protocol,
but is not a live model and makes no claim about model quality. Optional live
model calls incur provider charges and send the supplied text to that provider;
only enable them deliberately in your own deployment. Validation uses no live calls.

Python 3.11–3.13, Git, Make and Docker Compose are required. In this folder:

```sh
make install
make check
export TRIAGE_TOKEN=local-development-example-token
make start
.venv/bin/python client.py create ticket-42
.venv/bin/python client.py analyze ticket-42 --event-id analyze-42 --text 'Please explain my invoice'
.venv/bin/python worker.py drain ticket-42
make inspect
```

Inspect any ticket with `client.py read ticket-42`. The offline fixture returns
`billing` for invoice requests and `support` otherwise, with a short excerpt as
the summary. Its deterministic behavior supports local restart tests; it is not
an AI substitute. For a real model configure the worker's environment before drain:

```sh
export TRIAGE_LIVE_MODEL=1
export OPENAI_API_KEY=your-own-provider-key
export TRIAGE_MODEL=gpt-4.1-mini
```

Provider clients, HTTP responses, completion objects and MCP SDK objects remain
private native implementation. Machine state contains only declared JSON values.
Both the model tool and its consumer validate the result shape and category before
reporting. Invalid output or transport failure leaves the workflow waiting for a
valid declared result. A model call may have happened even when its response is
unknown. This application cannot guarantee a single provider invocation across
that uncertainty; after a result is saved, retries reuse it without calling the
model again. Preserve event IDs, arguments, journals and endpoint across retries.

One foreground worker owns this local journal. Its durable destination check,
retained intent identity, saved outcome and complete public command phases support
honest local recovery. This example advertises no verified §19 helper, leases,
distributed exactly-once or distributed ACID. The remote host's minimal profile
retains its outbox as intent evidence; local reported rows prevent repeating
completed work without claiming unsupported acknowledgement APIs.

For debugging inspect the supported remote checkpoint and local `outcomes` table.
Export a retained result with:

```sh
.venv/bin/python worker.py export EFFECT_ID --output var/analysis.json
```

Restart with the same journals. Stop workers and the host before `make reset`;
reset deletes disposable local and Docker data. Regenerate the hashed dependency
lock and rerun tests after updates. This folder owns all source, machine definitions,
locks, exact public candidate pin and Docker configuration. No sibling/root runtime
or build dependency, release, tag or available managed service is assumed.
Use real TLS and deployment authentication before exposing the loopback host.
