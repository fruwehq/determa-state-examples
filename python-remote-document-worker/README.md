# Customer-native worker with remote execution

A complete document workflow using the unreleased 0.3.0 public v1 host/client.
The HTTP/Docker host owns machine execution and durable SQLite checkpoints. A
separate customer worker reads committed `document_requested` intents, constructs
native ZIP archives, durably saves its JSON result and artifact locally, then sends
the correlated `document_ready` event to the remote machine.

Python 3.11–3.13, Git, Make and Docker Compose are required. From this folder:

```sh
make install
make check
make container-check
export DOCUMENT_TOKEN=local-development-example-token
make start
.venv/bin/python client.py create document-42
.venv/bin/python client.py build document-42 --event-id build-42 --text 'Customer-owned content'
.venv/bin/python worker.py drain document-42
make inspect
```

The machine reaches `available` only after the declared result is remotely admitted
and processed. Native `BytesIO`, `ZipFile` and archive bytes remain inside the worker.
Only a declared digest string and byte count enter portable version-1 state. The
worker owns arbitrary native implementation; the machine owns when and what-next.

The worker binds its destination durably, compares retained intent bytes for
conflicts, and stores a result before reporting it. A lost remote response is resumed
through the public client's saved endpoint/binding and complete admission/processing
requests. Repeated drains reuse the local outcome and avoid rebuilding the artifact.
One foreground worker process owns this disposable journal; it does not advertise
verified §19 helpers, leases/fencing, distributed exactly-once or distributed ACID.
The public host advertises its actual minimal core profile only.

The remote outbox remains retained intent evidence: this example does not falsely
acknowledge it through an unsupported helper operation. Local worker `reported`
records distinguish finished reports when polling that retained evidence. The local
journal and archives are customer data, outside remote persistence and portable
machine snapshots. Back them up through your application's own policy.

For debugging, read the supported remote checkpoint with `make inspect`; examine
the local `outcomes` table to obtain an effect ID, then export its archive:

```sh
.venv/bin/python worker.py export EFFECT_ID --output var/document.zip
```

Stop the host/workers before `make reset`. Do not delete live checkpoint, client
receipt or outcome rows. Complete hashed dependencies and exact public engine pins
are local to this folder; no release/tag or available managed service is assumed.
Regenerate locks and rerun tests after dependency changes. Configure real transport
TLS and authentication before exposing this loopback reference deployment.
