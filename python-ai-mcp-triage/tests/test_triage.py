import json
import sqlite3
import threading
from contextlib import contextmanager

import httpx
import pytest

from client import TriageClient, http_transport
from model_service import analyze_support
from server import make_server
from worker import TriageWorker


@contextmanager
def running(path):
    server = make_server(path, "local-test-token", ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/operations"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_analysis(app):
    app.create("ticket-42")
    return app.event("ticket-42", "analyze", "analyze-42",
                     {"text": "Please explain my invoice", "request_id": "ticket-42:analysis"})


def test_ai_worker_calls_real_mcp_after_commit_and_processes_declared_result(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIAGE_LIVE_MODEL", raising=False)
    with running(tmp_path / "host.sqlite3") as endpoint:
        app = TriageClient(tmp_path / "client.sqlite3", endpoint, "local-test-token")
        initial = request_analysis(app)
        calls = []
        def model(payload):
            assert app.read("ticket-42") == initial
            assert initial["pending_outbox_intents"]
            calls.append(payload)
            return TriageWorker.invoke_model(payload)
        worker = TriageWorker(tmp_path / "worker.sqlite3", app, builder=model)
        assert worker.drain("ticket-42") == 1
        assert worker.drain("ticket-42") == 0
        assert len(calls) == 1
        with sqlite3.connect(worker.path) as connection:
            result, reported = connection.execute(
                "SELECT outcome,reported FROM outcomes").fetchone()
        assert json.loads(result) == {"category": "billing", "summary": "Please explain my invoice"}
        assert reported == 1
        final = app.read("ticket-42")
        assert final != initial
        reopened = TriageWorker(worker.path, app, builder=lambda _: pytest.fail("model re-invoked"))
        assert reopened.drain("ticket-42") == 0


def test_lost_result_admission_reuses_saved_model_result(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIAGE_LIVE_MODEL", raising=False)
    with running(tmp_path / "host.sqlite3") as endpoint:
        app = TriageClient(tmp_path / "user.sqlite3", endpoint, "local-test-token")
        request_analysis(app)
        original = http_transport("local-test-token")
        lost = True
        def send(address, candidate):
            nonlocal lost
            result = original(address, candidate)
            if candidate["operation"] == "admit" and lost:
                lost = False
                raise TimeoutError("lost model-result admission")
            return result
        app = TriageClient(tmp_path / "client.sqlite3", endpoint, "ignored", transport=send)
        worker = TriageWorker(tmp_path / "worker.sqlite3", app)
        with pytest.raises(TimeoutError):
            worker.drain("ticket-42")
        reopened = TriageWorker(worker.path, app, builder=lambda _: pytest.fail("model re-invoked"))
        assert reopened.drain("ticket-42") == 1
        assert reopened.drain("ticket-42") == 0


def test_offline_native_sdk_result_and_invalid_provider_result(monkeypatch):
    monkeypatch.delenv("TRIAGE_LIVE_MODEL", raising=False)
    assert analyze_support("Help") == {"category": "support", "summary": "Help"}
    original = __import__("model_service").offline_response
    def malformed(request):
        response = original(request)
        document = response.json()
        document["choices"][0]["message"]["content"] = '{"category":"invalid"}'
        return httpx.Response(200, json=document)
    monkeypatch.setattr("model_service.offline_response", malformed)
    with pytest.raises((ValueError, KeyError)):
        analyze_support("Help")
