"""Native MCP objects remain private; extract only declared JSON result fields."""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def _analyze(text):
    parameters = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / "model_service.py")], env=dict(os.environ))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("analyze_support", {"text": text})
            if result.isError:
                raise ValueError("model tool failed; no declared result")
            value = json.loads(result.content[0].text)
            if (not isinstance(value, dict) or set(value) != {"category", "summary"}
                or value["category"] not in {"billing", "support"}
                or not isinstance(value["summary"], str)):
                raise ValueError("invalid model result")
            return value


def analyze(text):
    return asyncio.run(_analyze(text))
