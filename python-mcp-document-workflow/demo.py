"""Use actual MCP stdio messages as a no-code tool consumer."""
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main():
    parameters = StdioServerParameters(command=sys.executable,
                                      args=[str(ROOT / "gateway.py")], env=dict(os.environ))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            if len(tools.tools) != 4:
                raise RuntimeError("expected all four workflow tools")
            steps = [
                ("create_document", {"document_id": "mcp-document-42"}),
                ("request_document", {"document_id": "mcp-document-42",
                                      "event_id": "mcp-build-42", "text": "No-code document"}),
                ("run_document_connector", {"document_id": "mcp-document-42"}),
                ("inspect_document", {"document_id": "mcp-document-42"}),
            ]
            for tool, arguments in steps:
                result = await session.call_tool(tool, arguments)
                if result.isError:
                    raise RuntimeError(result.content)
                print(f"{tool}: completed")


if __name__ == "__main__":
    asyncio.run(main())
