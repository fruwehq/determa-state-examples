"""Validate the standalone container's workflow and durable restart."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def docker(*arguments: str) -> None:
    subprocess.run(["docker", *arguments], check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    image = "determa-order-service:e2e"
    identity = f"determa-order-e2e-{os.getpid()}"
    port = os.environ.get("ORDER_E2E_PORT", "18086")
    base = f"http://127.0.0.1:{port}"

    def request(path: str, body: dict[str, Any] | None = None, key: str | None = None) -> Any:
        headers = {"content-type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(
            urllib.request.Request(base + path, data=data, headers=headers), timeout=10
        ) as response:
            return json.load(response)

    def start() -> None:
        docker(
            "run", "-d", "--name", identity, "-p", f"{port}:8000", "-v", f"{identity}:/data", image
        )
        for _ in range(30):
            try:
                assert request("/openapi.json")["info"]["version"] == "0.3.0"
                return
            except (urllib.error.URLError, TimeoutError, ConnectionResetError):
                time.sleep(1)
        raise RuntimeError("container did not become ready")

    if os.environ.get("EXAMPLE_PREBUILT_IMAGE") != "1":
        subprocess.run(["docker", "build", "-t", image, str(root)], check=True)
    docker("volume", "create", identity)
    try:
        start()
        created = request(
            "/orders", {"customer_id": "container", "amount_cents": 12900}, "create-container"
        )
        order_id = created["order_id"]
        assert request(
            "/orders", {"customer_id": "container", "amount_cents": 12900}, "create-container"
        )["duplicate"]
        assert request("/admin/outbox/deliver", {}) == {"attempted": 1, "newly_delivered": 1}
        assert request("/admin/outbox/deliver", {}) == {"attempted": 1, "newly_delivered": 0}
        for event in ("payment_accepted", "fulfillment_started", "fulfillment_succeeded"):
            completed = request(f"/orders/{order_id}/events/{event}", {}, f"container:{event}")
        assert completed["lifecycle_status"] == "completed"
        docker("stop", identity)
        docker("rm", identity)
        start()
        assert request(f"/orders/{order_id}")["lifecycle_status"] == "completed"
        assert len(request(f"/admin/outbox?order_id={order_id}")) == 2
        print("Container workflow, idempotency, outbox, and restart checks passed.")
    finally:
        subprocess.run(["docker", "rm", "-f", identity], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", identity], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
