# No-code document workflow through MCP

This standalone reference application lets an MCP tool consumer create, request,
run and inspect a complete document workflow without writing machine integration
code. It uses the official public MCP SDK over stdio and the unreleased Determa
State 0.3.0 version-1 client over HTTP. The remote host executes the machine and
persists checkpoints. The application's reference connector owns native ZIP work
and its durable local outcome journal; its declared digest and byte count are
reported to the remote machine only after intent commit.

“Application-managed” means managed by this reference application. It is not a
claim that a private SaaS connector or managed service is available. One foreground
MCP server owns its connector journal; this example claims neither verified §19
helpers nor distributed exactly-once, leases or distributed ACID.

Python 3.11–3.13, Git, Make and Docker Compose are required. In this folder:

```sh
make install
make check
export DOCUMENT_TOKEN=local-development-example-token
make start
make demo
make inspect
```

`make demo` uses real MCP initialization, tool discovery and tool calls. The four
tools are `create_document`, `request_document`, `run_document_connector` and
`inspect_document`. Preserve document IDs, event IDs and arguments across retries.
The tool consumer supplies declared strings; credentials, native SDK objects and
archive bytes stay outside portable machine state. A conflicting event-ID reuse
is refused. An MCP tool error is not evidence that no remote commit occurred:
retry with the same identity and original arguments to reconcile saved phases.

Configure a no-code MCP consumer with an absolute executable and script path:

```json
{
  "mcpServers": {
    "documents": {
      "command": "/absolute/path/python-mcp-document-workflow/.venv/bin/python",
      "args": ["/absolute/path/python-mcp-document-workflow/gateway.py"],
      "env": {
        "DOCUMENT_ENDPOINT": "http://127.0.0.1:8089/v1/operations",
        "DOCUMENT_TOKEN": "local-development-example-token",
        "DOCUMENT_DATA_DIR": "/absolute/path/python-mcp-document-workflow/var"
      }
    }
  }
}
```

The stdio MCP server emits protocol messages only on stdout. The HTTP host is
loopback-only in Compose; use real TLS and deployment authentication before
exposing it. Remote machine execution is separate from connector execution. The
minimal host does not advertise timer, authority, archive or effects helper APIs.
The remote outbox stays retained intent evidence; local reported rows prevent
rebuilding on repeated polls without inventing an unsupported acknowledgement.

For debugging use `make inspect`, inspect the local connector `outcomes` table,
and export an archive with `.venv/bin/python worker.py export EFFECT_ID --database var/connector.sqlite3
--output var/document.zip`.
Restart using the same data directory and endpoint. Stop the MCP consumer and
host before `make reset`; reset deletes disposable local journals and Docker data.
Keep retained results and request journals together in production. Regenerate the
hashed lock and rerun tests when updating dependencies. This folder owns every
source file, machine, manifest, lock, source pin and deployment configuration it
needs; no sibling or root runtime/build dependency is required. No release or tag
is assumed.
