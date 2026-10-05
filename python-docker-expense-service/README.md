# Docker-hosted expense approval

A complete expense submission and approval workflow using the unreleased 0.3.0
version-1 public client and local SQLite host. The client and host use actual
`determa.execution_host` messages over authenticated HTTP. The same public contract
is intended for future hosted deployments; this example runs locally and does not
claim an available Fruwe managed service.

Python 3.11–3.13, Git, Make, and Docker Compose are required. From this folder:

```sh
make install
make check
export EXPENSE_TOKEN=local-development-example-token
make start
.venv/bin/python client.py create expense-42
.venv/bin/python client.py submit expense-42 --event-id submit-42 --amount-cents 12900
.venv/bin/python client.py approve expense-42 --event-id approve-42
make inspect
```

For a host without Docker, run `make host` in another terminal with the same token.
Tokens authenticate the HTTP adapter and never enter portable machine values.
The Compose port is bound to loopback. Configure TLS and your own authentication
policy before exposing an adapter outside this local environment.

The machine owns draft, pending, approved and rejected states. SQLite commits
mailbox acceptance separately from processing, with checkpoint revision/digest
preconditions. The host atomically saves the checkpoint and complete first public
response. The client durably saves request bytes, endpoint and scope binding
before sending a mutation. Neither a remote request nor its response participates
in your caller's database transaction.

An application command journal also preserves both admission and processing
requests. Repeat a workflow command with the same event ID and identical content
to resume those saved phases; changed ID reuse is refused before transport.

A timeout means unknown outcome. Preserve the client journal and query or retry
the saved operation, using the stable operation IDs printed in requests:

```sh
.venv/bin/python client.py receipt expense-42:create
.venv/bin/python client.py retry expense-42:create
.venv/bin/python client.py receipt submit-42:admit
.venv/bin/python client.py retry submit-42:process
```

The host advertises only its implemented core operations and structural inspection;
it does not claim timers, authority, archive/recovery or native-effect helper profiles.
A deployment alias change applies to new work. Saved requests keep the original
endpoint and binding. Restart the Compose host against its named volume to retain
checkpoints and operation responses. A replacement host at a different endpoint
needs explicit new routing for new work; do not retarget saved mutations.

`make check` covers actual HTTP workflow completion, native SQLite restart,
authentication before existence disclosure, and a lost committed response followed
by receipt lookup and exact retry after client restart. Dependencies are hashed,
and the installer checks an exact allowlisted public Git engine commit. Machine
YAML retains numeric `format: 1`; persisted artifacts use version 1. No release or
tag is assumed.

Stop clients before `make reset`, which removes this disposable host volume and
local client journals. Inspect supported checkpoint data through the API instead
of editing database rows. Regenerate the hashed lockfile with pip-tools after
changing third-party dependencies; update both source pins and rerun checks after
an engine update. This local example does not provide a distributed coordinator,
remote database transaction, automatic retry service or managed control plane.
