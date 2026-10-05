import json
import sqlite3
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import AlreadyExists
from google.auth.credentials import AnonymousCredentials
from google.cloud import tasks_v2

from app import CloudTasksHandler, Workflow


def setup(tmp_path):
    client = tasks_v2.CloudTasksClient(credentials=AnonymousCredentials())
    handler = CloudTasksHandler(client, "projects/demo/locations/us-central1/queues/demo")
    workflow = Workflow(tmp_path / "workflow.sqlite3", {"cloud-tasks.v1": handler})
    return client, handler, workflow


def test_sdk_proto_objects_are_private_and_dispatch_follows_commit(tmp_path):
    client, _, workflow = setup(tmp_path)
    calls = []

    def create_task(*, request):
        assert isinstance(request, tasks_v2.CreateTaskRequest)
        assert isinstance(request.task, tasks_v2.Task)
        assert isinstance(request.task.http_request, tasks_v2.HttpRequest)
        assert request.task.http_request.body == b'{"order":42}'
        with sqlite3.connect(workflow.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
        calls.append(request)
        return request.task

    client.create_task = Mock(side_effect=create_task)
    workflow.enqueue("order-42", "https://example.com/dispatch", '{"order":42}')
    assert calls == []
    assert workflow.drain() == 1
    assert workflow.drain() == 0
    reopened = Workflow(workflow.path, workflow.handlers)
    assert reopened.drain() == 0
    aggregate = reopened.inspect()[0]["aggregate"]
    serialized = json.dumps(aggregate)
    assert "projects/demo/locations" in serialized  # declared task-name string only
    assert aggregate["aggregate_state_schema_version"] == 1
    with sqlite3.connect(workflow.path) as connection:
        assert json.loads(connection.execute("SELECT outcome FROM effects").fetchone()[0]) == {
            "task_name": calls[0].task.name
        }


def test_sdk_failure_preserves_committed_pending_intent_for_restart(tmp_path):
    client, handler, workflow = setup(tmp_path)
    client.create_task = Mock(side_effect=RuntimeError("transport unavailable"))
    workflow.enqueue("retry", "https://example.com/dispatch", "{}")
    before = workflow.inspect()
    with pytest.raises(RuntimeError, match="unavailable"):
        workflow.drain()
    assert workflow.inspect() == before
    client.create_task = Mock(side_effect=lambda *, request: request.task)
    reopened = Workflow(workflow.path, {"cloud-tasks.v1": handler})
    assert reopened.drain() == 1


def test_existing_task_is_checked_before_recording_submission(tmp_path):
    client, _, workflow = setup(tmp_path)
    workflow.enqueue("existing", "https://example.com/dispatch", "{}")
    saved = []

    def lost_response(*, request):
        saved.append(request.task)
        raise AlreadyExists("retained task")

    client.create_task = Mock(side_effect=lost_response)
    client.get_task = Mock(side_effect=lambda *, request: saved[-1])
    assert workflow.drain() == 1
    assert client.get_task.call_args.kwargs["request"].response_view == tasks_v2.Task.View.FULL


def test_conflicting_existing_task_cannot_advance_workflow(tmp_path):
    client, _, workflow = setup(tmp_path)
    workflow.enqueue("conflict", "https://example.com/dispatch", "{}")
    client.create_task = Mock(side_effect=AlreadyExists("conflict"))
    client.get_task = Mock(
        return_value=tasks_v2.Task(http_request=tasks_v2.HttpRequest(url="https://other.example"))
    )
    with pytest.raises(ValueError, match="differs"):
        workflow.drain()
    with sqlite3.connect(workflow.path) as connection:
        assert connection.execute("SELECT outcome FROM effects").fetchone()[0] is None


def test_restart_and_mutated_registry_cannot_retarget_pending_effect(tmp_path):
    client, handler, workflow = setup(tmp_path)
    workflow.enqueue("pinned", "https://example.com/dispatch", "{}")
    replacement = CloudTasksHandler(client, "projects/other/locations/us-central1/queues/other")
    with pytest.raises(ValueError, match="immutable binding"):
        Workflow(workflow.path, {"cloud-tasks.v1": replacement})
    handler.queue = replacement.queue
    client.create_task = Mock()
    with pytest.raises(ValueError, match="immutable binding"):
        workflow.drain()
    client.create_task.assert_not_called()


def test_handler_must_be_bound_before_committing_any_work(tmp_path):
    with pytest.raises(ValueError, match="before creating work"):
        Workflow(tmp_path / "unbound.sqlite3", {})
    assert not (tmp_path / "unbound.sqlite3").exists()
    _, _, workflow = setup(tmp_path)
    workflow.handlers.clear()
    with pytest.raises(ValueError, match="not installed"):
        workflow.enqueue("unbound", "https://example.com/dispatch", "{}")
    with sqlite3.connect(workflow.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0


def test_existing_unbound_intent_cannot_receive_a_late_destination(tmp_path):
    _, handler, workflow = setup(tmp_path)
    workflow.enqueue("orphan", "https://example.com/dispatch", "{}")
    # Model an older/corrupt application database; no production bypass is offered.
    with sqlite3.connect(workflow.path) as connection:
        connection.execute("DROP TRIGGER bindings_no_delete")
        connection.execute("DELETE FROM handler_bindings")
    with pytest.raises(ValueError, match="unbound intent"):
        Workflow(workflow.path, {"cloud-tasks.v1": handler})
    with sqlite3.connect(workflow.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM handler_bindings").fetchone()[0] == 0
