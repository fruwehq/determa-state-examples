import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_worker import running

from gateway import ROOT


def test_real_mcp_workflow_with_application_managed_connector(tmp_path):
    async def use(endpoint):
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "gateway.py")],
            env={**os.environ, "DOCUMENT_ENDPOINT": endpoint,
                 "DOCUMENT_TOKEN": "local-test-token", "DOCUMENT_DATA_DIR": str(tmp_path / "data")},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "create_document", "request_document",
                    "run_document_connector", "inspect_document"}
                async def call(name, **arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result.content
                    return json.loads(result.content[0].text)
                await call("create_document", document_id="mcp-42")
                before = await call("request_document", document_id="mcp-42",
                                    event_id="build-42", text="MCP customer document")
                assert before["pending_outbox_intents"]
                result = await call("run_document_connector", document_id="mcp-42")
                assert result["reported"] == 1
                assert (await call("run_document_connector", document_id="mcp-42"))["reported"] == 0
                assert await call("inspect_document", document_id="mcp-42") == result["checkpoint"]
                conflict = await session.call_tool("request_document", {
                    "document_id": "mcp-42", "event_id": "build-42", "text": "changed"})
                assert conflict.isError
    with running(tmp_path / "host.sqlite3") as endpoint:
        asyncio.run(use(endpoint))
