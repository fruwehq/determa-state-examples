"""Authenticated HTTP adapter for the actual version-1 SQLite public host."""

from __future__ import annotations

import argparse
import hmac
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import determa.state as ds
from determa.state.errors import ArtifactError
from determa.state.public_host import SQLitePublicExecutionHost
from determa.state.wire import canonical_bytes, strict_json

ROOT = Path(__file__).resolve().parent


def make_server(path: Path, token: str, address: tuple[str, int]) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("a deployment token is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = ds.load_bundle((ROOT / "machines/document.yaml").read_text())
    host = SQLitePublicExecutionHost(
        path,
        scope_alias="documents",
        scope_binding_identity="documents-local-v1",
        authorized_principals=frozenset({"document-app"}),
        resolver=ds.MemoryArtifactResolver(definitions={bundle.fingerprint: bundle}),
    )
    host.setup_schema()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Do not log credentials or machine payloads.

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(401, "authentication required")
                return
            if self.path != "/v1/operations":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_048_576:
                    raise ValueError("invalid body length")
                request, _ = strict_json(self.rfile.read(length))
                response = host.handle(request, principal="document-app")
                raw = canonical_bytes(response)
            except (ValueError, ArtifactError):
                self.send_error(400, "invalid public request")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "var/host.sqlite3")
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    server = make_server(args.database, os.environ["DOCUMENT_TOKEN"], (args.address, args.port))
    print(f"Expense host listening on {server.server_address}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
