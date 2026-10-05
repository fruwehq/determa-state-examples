# Embedded Cloud Tasks workflow

A standalone unreleased Determa State 0.3.0 application that commits a task-request
intent in SQLite, invokes a named native handler, and admits the declared
`task_created` result. The handler calls the real Google Cloud Tasks SDK and builds
its actual `Task`, `HttpRequest`, and `CreateTaskRequest` protobuf values. Those
objects stay inside the handler; the workflow receives only a task-name string.

Python 3.11–3.13, Git and Make are required. From this folder:

```sh
make install
make check
make start
make inspect
```

`make start` is an offline complete workflow using the real SDK/protobuf classes
and mocked transport. It makes no cloud request and requires no credentials.
Installation verifies the exact public engine commit in `source-lock.json` after
installing hashed third-party dependencies. No 0.3.0 tag or release is assumed.
Machine YAML remains numeric `format: 1`; persisted aggregates use version 1.

The application owns the SQLite transaction containing the aggregate and outbox.
`enqueue` commits before `drain` calls a handler. A failed SDK call leaves the
intent pending for a later drain or restart. Success records the declared result
and advances the machine in one new SQLite transaction. Task submission means
Cloud Tasks accepted the task, not that the HTTP callback completed its business
work. Real callback success would require another declared input.

The handler uses a stable task name derived from the effect ID. If Cloud Tasks
reports `AlreadyExists`, it reads the retained task with the FULL view and verifies
its HTTP request before recording submission. Cloud Tasks retention is finite;
this is not a distributed exactly-once guarantee. This application-owned adapter
does not advertise the stronger verified §19 committed-native-effect profile,
leases, fencing, a distributed coordinator, or a managed service.

To integrate a real queue in your own deployment, construct `CloudTasksClient`
with your Google authentication configuration and pass it to `CloudTasksHandler`
with the queue resource name. Keep credentials and destination configuration in
application deployment code. Authorize cloud mutations separately; the default
CLI and tests remain offline.

For debugging, `make inspect` validates the readable JSON artifact boundary;
SQLite stores `workflows` and `effects`, including pending versus recorded outcomes.
Stop the process before `make reset`. For dependency maintenance, regenerate
`requirements.lock.txt` with pip-tools and hashes, update the exact source pin in
both manifests, and rerun all checks. Never edit committed intents to change a
pending task's destination. This example supports one foreground drain process.
