"""Reference application-managed MCP tools for a complete remote workflow."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from client import DocumentClient
from worker import DocumentWorker

ROOT = Path(__file__).resolve().parent
mcp = FastMCP("Determa reference document connector")


def application():
    directory = Path(os.environ.get("DOCUMENT_DATA_DIR", str(ROOT / "var")))
    app = DocumentClient(
        directory / "gateway-client.sqlite3",
        os.environ.get("DOCUMENT_ENDPOINT", "http://127.0.0.1:8089/v1/operations"),
        os.environ["DOCUMENT_TOKEN"],
    )
    return app, directory


@mcp.tool()
def create_document(document_id: str) -> dict:
    """Create a remote document workflow; repeat with the same document ID to reconcile."""
    app, _ = application()
    return app.create(document_id)


@mcp.tool()
def request_document(document_id: str, event_id: str, text: str) -> dict:
    """Commit document work. Preserve event_id and text when retrying this command."""
    app, _ = application()
    return app.event(document_id, "build", event_id,
                     {"text": text, "request_id": f"{document_id}:document"})


@mcp.tool()
def run_document_connector(document_id: str) -> dict:
    """Run this application's native connector after remote intent commit."""
    app, directory = application()
    worker = DocumentWorker(directory / "connector.sqlite3", app)
    return {"reported": worker.drain(document_id), "checkpoint": app.read(document_id)}


@mcp.tool()
def inspect_document(document_id: str) -> dict:
    """Read the remotely persisted workflow checkpoint."""
    app, _ = application()
    return app.read(document_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
