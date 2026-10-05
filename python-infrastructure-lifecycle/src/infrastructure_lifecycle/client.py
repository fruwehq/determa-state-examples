"""Foreground bridge to the separately running, durable local destination.

Transport failure leaves acceptance unknown. This bridge never retries, resets the
destination, or promotes missing native evidence into permission to execute again.
"""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Self

from .provider import ProviderError, canonical, parse

_LIMIT = 1024 * 1024


class ProviderTransportError(RuntimeError):
    """The caller must retain ambiguity and reconcile through native inspection."""


class ProviderProcess:
    def __init__(self, database: Path, scope: str, *, timeout: float = 5.0) -> None:
        if not scope or not 0 < timeout <= 60:
            raise ValueError("configured scope and bounded timeout required")
        self.database = database.resolve()
        self.scope = scope
        self.timeout = timeout
        self._lock = threading.Lock()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "infrastructure_lifecycle.provider",
                "--database",
                str(self.database),
                "--scope",
                scope,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            bufsize=0,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)
        self._closed = False

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        # Configured authorization precedes transport and retained disclosure.
        if request.get("scope_identity") != self.scope:
            raise ProviderError("unauthorized_scope")
        data = canonical(request) + b"\n"
        if len(data) > _LIMIT:
            raise ValueError("provider request exceeds transport limit")
        with self._lock:
            if self._closed:
                raise ProviderTransportError("delivery_ambiguous")
            deadline = time.monotonic() + self.timeout
            try:
                assert (
                    self._process.stdin is not None and self._process.stdout is not None
                )
                with selectors.DefaultSelector() as selector:
                    selector.register(self._process.stdin, selectors.EVENT_WRITE)
                    written = 0
                    while written < len(data):
                        self._wait(selector, deadline)
                        written += os.write(
                            self._process.stdin.fileno(), data[written:]
                        )
                    selector.unregister(self._process.stdin)
                    selector.register(self._process.stdout, selectors.EVENT_READ)
                    response = bytearray()
                    while b"\n" not in response:
                        self._wait(selector, deadline)
                        chunk = os.read(self._process.stdout.fileno(), 65536)
                        if not chunk:
                            raise EOFError("destination stopped before response")
                        response.extend(chunk)
                        if len(response) > _LIMIT:
                            raise ValueError(
                                "destination response exceeds transport limit"
                            )
                if not response.endswith(b"\n") or response.count(b"\n") != 1:
                    raise ValueError("unexpected destination response framing")
                result = parse(bytes(response))
                if not isinstance(result, dict) or not isinstance(
                    result.get("status"), str
                ):
                    raise TypeError("invalid destination response")
                if result["status"] == "refused" and (
                    set(result) != {"status", "code"}
                    or not isinstance(result["code"], str)
                ):
                    raise TypeError("invalid destination refusal")
            except (OSError, TypeError, ValueError, EOFError, TimeoutError) as error:
                self._close()
                raise ProviderTransportError("delivery_ambiguous") from error
            if result["status"] == "refused":
                raise ProviderError(result["code"])
            return result

    @staticmethod
    def _wait(selector: selectors.BaseSelector, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not selector.select(remaining):
            raise TimeoutError("destination response deadline elapsed")

    def inspect(self, effect_id: str) -> dict[str, Any]:
        return self.execute(
            {
                "operation": "inspect_operation",
                "scope_identity": self.scope,
                "effect_id": effect_id,
            }
        )

    def verify_retained(
        self, request: dict[str, Any], response: dict[str, Any]
    ) -> bool:
        if request.get("scope_identity") != self.scope:
            raise ProviderError("unauthorized_scope")
        expected = canonical(
            {"status": "committed", "request": request, "response": response}
        )
        actual = self.inspect(request["effect_id"])
        # Python equality conflates JSON booleans and integers; native retained
        # evidence binds their distinct serialized values.
        return canonical(actual) == expected

    def _close(self) -> None:
        self._closed = True
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait(timeout=5)
        for pipe in (self._process.stdin, self._process.stdout):
            if pipe is not None:
                pipe.close()

    def close(self) -> None:
        with self._lock:
            self._close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
