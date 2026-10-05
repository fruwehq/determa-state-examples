import hashlib
import io
import sqlite3
import threading
import zipfile
from contextlib import contextmanager

import pytest

from client import DocumentClient, http_transport
from server import make_server
from worker import DocumentWorker


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


def create_work(app):
    app.create("document-42")
    return app.event(
        "document-42",
        "build",
        "build-42",
        {"text": "Customer content", "request_id": "document-42:document"},
    )


def test_native_archive_runs_after_remote_commit_and_returns_declared_result(tmp_path):
    with running(tmp_path / "host.sqlite3") as endpoint:
        app = DocumentClient(tmp_path / "client.sqlite3", endpoint, "local-test-token")
        checkpoint = create_work(app)
        calls = []

        def build(payload):
            observed = app.read("document-42")
            assert observed == checkpoint
            assert observed["pending_outbox_intents"]
            calls.append(payload)
            return DocumentWorker.build_archive(payload)

        worker = DocumentWorker(tmp_path / "worker.sqlite3", app, builder=build)
        assert worker.drain("document-42") == 1
        assert worker.drain("document-42") == 0
        assert len(calls) == 1
        with sqlite3.connect(worker.path) as connection:
            archive, outcome, reported = connection.execute(
                "SELECT archive,outcome,reported FROM outcomes"
            ).fetchone()
        with zipfile.ZipFile(io.BytesIO(archive)) as native_archive:
            assert native_archive.read("document.txt") == b"Customer content"
        import json

        assert (
            json.loads(outcome)["artifact_digest"]
            == "sha256:" + hashlib.sha256(archive).hexdigest()
        )
        assert reported == 1
        final = app.read("document-42")
        assert final["root_record"]["aggregate_state"]["runtimes"][0]["variables"]
        assert final[
            "pending_outbox_intents"
        ]  # retained evidence; no unsupported acknowledgement claim
        reopened = DocumentWorker(
            worker.path, app, builder=lambda _: pytest.fail("rebuilt a saved result")
        )
        assert reopened.drain("document-42") == 0


def test_unknown_result_admission_reuses_durable_outcome_after_restart(tmp_path):
    with running(tmp_path / "host.sqlite3") as endpoint:
        user = DocumentClient(tmp_path / "user.sqlite3", endpoint, "local-test-token")
        create_work(user)
        transport = http_transport("local-test-token")
        lose = True

        def send(address, candidate):
            nonlocal lose
            assert address == endpoint
            response = transport(address, candidate)
            if candidate["operation"] == "admit" and lose:
                lose = False
                raise TimeoutError("lost committed result admission")
            return response

        app = DocumentClient(
            tmp_path / "worker-client.sqlite3", endpoint, "ignored", transport=send
        )
        path = tmp_path / "worker.sqlite3"
        worker = DocumentWorker(path, app)
        with pytest.raises(TimeoutError):
            worker.drain("document-42")
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT reported FROM outcomes").fetchone()[0] == 0
        restarted_app = DocumentClient(
            tmp_path / "worker-client.sqlite3", endpoint, "ignored", transport=send
        )
        restarted = DocumentWorker(
            path, restarted_app, builder=lambda _: pytest.fail("outcome rebuilt")
        )
        assert restarted.drain("document-42") == 1
        assert restarted.drain("document-42") == 0


def test_worker_cannot_rebind_retained_destination(tmp_path):
    with running(tmp_path / "host.sqlite3") as endpoint:
        app = DocumentClient(tmp_path / "client.sqlite3", endpoint, "local-test-token")
        path = tmp_path / "worker.sqlite3"
        DocumentWorker(path, app)
        other = DocumentClient(tmp_path / "other.sqlite3", "unavailable-local-endpoint", "ignored")
        with pytest.raises(ValueError, match="cannot change"):
            DocumentWorker(path, other)


def test_mutated_destination_refused_before_transport_or_builder(tmp_path):
    calls = []
    app = DocumentClient(
        tmp_path / "client.sqlite3", "original-endpoint", "ignored",
        transport=lambda *args: calls.append(args),
    )
    worker = DocumentWorker(tmp_path / "worker.sqlite3", app,
                            builder=lambda _: pytest.fail("work redirected"))
    app.endpoint = "replacement-endpoint"
    with pytest.raises(ValueError, match="cannot change"):
        worker.drain("document-42")
    assert calls == []
