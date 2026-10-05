"""Application-owned model MCP tool; explicitly offline unless opted in."""
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

mcp = FastMCP("Support triage model")


def offline_response(request):
    # Fixture transport exercises native SDK response parsing; it is not a live model.
    data = json.loads(request.content)
    text = data["messages"][-1]["content"]
    result = {"category": "billing" if "invoice" in text.lower() else "support",
              "summary": text[:160]}
    return httpx.Response(200, json={
        "id": "offline-fixture", "object": "chat.completion", "created": 0,
        "model": "offline-fixture", "choices": [{"index": 0, "finish_reason": "stop",
          "message": {"role": "assistant", "content": json.dumps(result)}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


@mcp.tool()
def analyze_support(text: str) -> dict:
    """Return declared category and summary; default provider is an offline fixture."""
    live = os.environ.get("TRIAGE_LIVE_MODEL") == "1"
    native_http = None if live else httpx.Client(transport=httpx.MockTransport(offline_response))
    with OpenAI(api_key=os.environ["OPENAI_API_KEY"] if live else "offline-fixture",
                http_client=native_http) as client:
        response = client.chat.completions.create(
            model=os.environ.get("TRIAGE_MODEL", "gpt-4.1-mini") if live else "offline-fixture",
            messages=[{"role": "system", "content":
                       'Return JSON with category "billing" or "support", and a string summary.'},
                      {"role": "user", "content": text}],
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
    if (not isinstance(result, dict) or set(result) != {"category", "summary"}
        or result["category"] not in {"billing", "support"}
        or not isinstance(result["summary"], str)):
        raise ValueError("model result violates the declared application schema")
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
