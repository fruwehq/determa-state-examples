import threading
from contextlib import contextmanager
from urllib.error import HTTPError

import pytest

from client import ExpenseClient, http_transport, request
from server import make_server


@contextmanager
def running(path):
    server = make_server(path, "development-test-token", ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/operations"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def values(checkpoint):
    aggregate = checkpoint["root_record"]["aggregate_state"]
    root = next(r for r in aggregate["runtimes"] if r["runtime_id"] == aggregate["root_runtime_id"])
    return {
        v["variable_declaration_pointer"].rsplit("/", 1)[-1]: v["value"] for v in root["variables"]
    }


def test_remote_expense_approval_and_host_restart(tmp_path):
    path = tmp_path / "host.sqlite3"
    with running(path) as endpoint:
        app = ExpenseClient(tmp_path / "client.sqlite3", endpoint, "development-test-token")
        app.create("expense-42")
        assert values(app.event("expense-42", "submit", "submit-42", {"amount_cents": 12900}))[
            "decision"
        ] == ["string", "pending"]
        assert values(app.event("expense-42", "approve", "approve-42"))["decision"] == [
            "string",
            "approved",
        ]
    with running(path) as endpoint:
        # Fresh work uses the current endpoint; saved operations retain the old binding.
        app = ExpenseClient(tmp_path / "reader.sqlite3", endpoint, "development-test-token")
        assert values(app.read("expense-42"))["amount_cents"] == ["integer", "12900"]


def test_lost_http_response_reconciles_at_original_binding_after_client_restart(tmp_path):
    with running(tmp_path / "host.sqlite3") as endpoint:
        transport = http_transport("development-test-token")
        lost = True

        def send(address, candidate):
            nonlocal lost
            response = transport(address, candidate)
            if candidate["operation"] == "create" and lost:
                lost = False
                raise TimeoutError("lost committed HTTP response")
            return response

        app = ExpenseClient(tmp_path / "client.sqlite3", endpoint, "ignored", transport=send)
        with pytest.raises(TimeoutError):
            app.create("lost-expense")
        restarted = ExpenseClient(
            tmp_path / "client.sqlite3", "http://unavailable.invalid", "ignored", transport=send
        )
        observed = restarted.client.receipt("lost-expense:create")
        assert observed["value"]["result"]["retention"] == "retained"
        assert (
            restarted.client.retry("lost-expense:create")
            == observed["value"]["result"]["saved_response"]
        )


def test_authentication_rejects_before_revealing_existence(tmp_path):
    with running(tmp_path / "host.sqlite3") as endpoint:
        send = http_transport("incorrect")
        for identity in ("absent", "another"):
            with pytest.raises(HTTPError) as error:
                send(endpoint, request("read", identity))
            assert error.value.code == 401
