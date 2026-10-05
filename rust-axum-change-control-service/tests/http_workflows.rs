use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::path::Path;
use tempfile::TempDir;
use tower::ServiceExt;

use determa_change_control::{open_state, router};

struct TestApp {
    directory: TempDir,
    router: Router,
}

impl TestApp {
    fn new() -> Self {
        let directory = tempfile::tempdir().expect("temporary directory");
        let state =
            open_state(&directory.path().join("checkpoint.sqlite3")).expect("application state");
        Self {
            directory,
            router: router(state),
        }
    }

    fn restart(&mut self) {
        let state = open_state(&self.directory.path().join("checkpoint.sqlite3"))
            .expect("reopened application state");
        self.router = router(state);
    }

    async fn request(
        &self,
        method: &str,
        path: &str,
        role: &str,
        actor: &str,
        body: Value,
    ) -> (StatusCode, Value) {
        request(&self.router, method, path, role, actor, body).await
    }
}

async fn request(
    app: &Router,
    method: &str,
    path: &str,
    role: &str,
    actor: &str,
    body: Value,
) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header("content-type", "application/json")
                .header("x-actor-role", role)
                .header("x-actor-id", actor)
                .body(Body::from(body.to_string()))
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let bytes = response
        .into_body()
        .collect()
        .await
        .expect("response body")
        .to_bytes();
    let value = serde_json::from_slice(&bytes).expect("JSON response");
    (status, value)
}

#[tokio::test]
async fn health_identifies_the_candidate_engine() {
    let app = TestApp::new();
    let (status, health) = app
        .request("GET", "/health", "requester", "alice", json!({}))
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(health["determa_state"], "0.3.0");
}

async fn create(app: &TestApp, change_id: &str) -> Value {
    let (status, body) = app
        .request(
            "POST",
            "/changes",
            "requester",
            "alice",
            json!({
                "change_id": change_id,
                "creation_id": format!("create-{change_id}"),
                "title": "Rotate payment signing key"
            }),
        )
        .await;
    assert_eq!(status, StatusCode::CREATED);
    body["change"].clone()
}

fn command(operation_id: &str, change: &Value) -> Value {
    json!({
        "operation_id": operation_id,
        "expected_revision": change["revision"],
        "expected_checkpoint_digest": change["checkpoint_digest"]
    })
}

async fn post_command(
    app: &TestApp,
    path: &str,
    role: &str,
    actor: &str,
    body: Value,
) -> (StatusCode, Value) {
    app.request("POST", path, role, actor, body).await
}

#[tokio::test]
async fn approved_change_deploys_and_replays_stable_operations() {
    let app = TestApp::new();
    let change = create(&app, "chg-approved").await;
    let review_request = command("review-1", &change);
    let (status, review) = post_command(
        &app,
        "/changes/chg-approved/review",
        "reviewer",
        "bob",
        review_request.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(review["change"]["state"], "under_review");
    assert_eq!(review["change"]["variables"]["reviewer"], "bob");

    let (status, replay) = post_command(
        &app,
        "/changes/chg-approved/review",
        "reviewer",
        "bob",
        review_request,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(replay["replayed"], true);
    assert_eq!(replay["change"]["revision"], review["change"]["revision"]);

    let approve_request = command("approve-1", &review["change"]);
    let (status, approved) = post_command(
        &app,
        "/changes/chg-approved/approve",
        "reviewer",
        "bob",
        approve_request,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(approved["change"]["state"], "deploying");
    assert_eq!(approved["change"]["pending_outbox_count"], 1);

    let mut deployed_request = command("deployment-1", &approved["change"]);
    deployed_request["deployment_id"] = json!("prod-2026-09-09-001");
    let (status, deployed) = post_command(
        &app,
        "/changes/chg-approved/deployment/succeeded",
        "operator",
        "release-bot",
        deployed_request,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(deployed["change"]["state"], "deployed");
    assert_eq!(deployed["change"]["status"], "completed");

    let deployment_replay = {
        let mut value = command("deployment-1", &approved["change"]);
        value["deployment_id"] = json!("prod-2026-09-09-001");
        value
    };
    let (status, replay) = post_command(
        &app,
        "/changes/chg-approved/deployment/succeeded",
        "operator",
        "release-bot",
        deployment_replay,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(replay["replayed"], true);
    assert_eq!(replay["change"]["revision"], deployed["change"]["revision"]);
}

#[tokio::test]
async fn rejection_is_terminal_and_authorization_stays_in_the_application() {
    let app = TestApp::new();
    let change = create(&app, "chg-rejected").await;
    let (status, _) = post_command(
        &app,
        "/changes/chg-rejected/review",
        "requester",
        "alice",
        command("review-wrong-role", &change),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    let (_, review) = post_command(
        &app,
        "/changes/chg-rejected/review",
        "reviewer",
        "bob",
        command("review-2", &change),
    )
    .await;
    let mut rejection = command("reject-1", &review["change"]);
    rejection["reason"] = json!("Missing rollback evidence");
    let (status, rejected) = post_command(
        &app,
        "/changes/chg-rejected/reject",
        "reviewer",
        "carol",
        rejection,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(rejected["change"]["state"], "rejected");
    assert_eq!(rejected["change"]["status"], "completed");
    assert_eq!(
        rejected["change"]["variables"]["rejection_reason"],
        "Missing rollback evidence"
    );

    let (status, terminal) = post_command(
        &app,
        "/changes/chg-rejected/approve",
        "reviewer",
        "carol",
        command("late-approve", &rejected["change"]),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(terminal["error"], "terminal_change");
}

#[tokio::test]
async fn failed_deployment_requests_and_completes_rollback() {
    let app = TestApp::new();
    let change = create(&app, "chg-rollback").await;
    let (_, review) = post_command(
        &app,
        "/changes/chg-rollback/review",
        "reviewer",
        "bob",
        command("review-3", &change),
    )
    .await;
    let (_, approved) = post_command(
        &app,
        "/changes/chg-rollback/approve",
        "reviewer",
        "bob",
        command("approve-3", &review["change"]),
    )
    .await;
    let mut failure = command("deployment-failed-1", &approved["change"]);
    failure["reason"] = json!("Canary error rate exceeded threshold");
    let (status, rollback) = post_command(
        &app,
        "/changes/chg-rollback/deployment/failed",
        "operator",
        "release-bot",
        failure,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(rollback["change"]["state"], "rollback_pending");
    assert_eq!(rollback["change"]["pending_outbox_count"], 2);

    let (status, completed) = post_command(
        &app,
        "/changes/chg-rollback/rollback/succeeded",
        "operator",
        "release-bot",
        command("rollback-1", &rollback["change"]),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(completed["change"]["state"], "rolled_back");
    assert_eq!(completed["change"]["status"], "completed");
}

#[tokio::test]
async fn stale_revision_conflicts_and_changed_operation_reuse_are_rejected() {
    let app = TestApp::new();
    let change = create(&app, "chg-conflict").await;
    let stale = command("stale-review", &change);
    let (_, review) = post_command(
        &app,
        "/changes/chg-conflict/review",
        "reviewer",
        "bob",
        command("review-4", &change),
    )
    .await;

    let (status, conflict) = post_command(
        &app,
        "/changes/chg-conflict/review",
        "reviewer",
        "bob",
        stale,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(conflict["error"], "revision_conflict");

    let approve = command("decision-shared", &review["change"]);
    let (_, approved) = post_command(
        &app,
        "/changes/chg-conflict/approve",
        "reviewer",
        "bob",
        approve.clone(),
    )
    .await;
    let mut different = approve;
    different["reason"] = json!("same id, different command body");
    let (status, operation_conflict) = post_command(
        &app,
        "/changes/chg-conflict/reject",
        "reviewer",
        "bob",
        different,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(operation_conflict["error"], "operation_id_conflict");
    assert_eq!(approved["change"]["state"], "deploying");
}

#[tokio::test]
async fn checkpoint_and_outbox_survive_process_restart() {
    let mut app = TestApp::new();
    let change = create(&app, "chg-restart").await;
    let (_, review) = post_command(
        &app,
        "/changes/chg-restart/review",
        "reviewer",
        "bob",
        command("review-5", &change),
    )
    .await;
    let (_, approved) = post_command(
        &app,
        "/changes/chg-restart/approve",
        "reviewer",
        "bob",
        command("approve-5", &review["change"]),
    )
    .await;
    app.restart();

    let (status, restored) = app
        .request(
            "GET",
            "/changes/chg-restart",
            "requester",
            "alice",
            json!({}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(restored["revision"], approved["change"]["revision"]);
    assert_eq!(restored["state"], "deploying");

    let (status, outbox) = app
        .request(
            "GET",
            "/changes/chg-restart/outbox",
            "operator",
            "release-bot",
            json!({}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        outbox["pending"].as_array().expect("pending array").len(),
        1
    );
    assert_eq!(
        outbox["pending"][0]["intent"]["event"],
        "deployment_requested"
    );
}

#[test]
fn example_folder_contains_every_runtime_input() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for relative in [
        "Cargo.toml",
        "Cargo.lock",
        "machines/change-control.yaml",
        "Dockerfile",
        "README.md",
        "Makefile",
    ] {
        assert!(root.join(relative).is_file(), "missing {relative}");
    }
}
