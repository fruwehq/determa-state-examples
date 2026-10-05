use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use determa_state::{
    checkpoint::{
        CheckpointHost, DurableStoreMode, ExecutionCheckpoint, ExecutionStore, MutationGuard,
        SqliteExecutionStore,
    },
    load_bundle, restore_aggregate, ArtifactError, Bindings, Bundle, InMemoryDefinitionResolver,
    TypedValue, Value,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value as JsonValue};
use std::{collections::BTreeMap, path::Path as FilePath, sync::Arc};

const MACHINE_SOURCE: &str = include_str!("../machines/change-control.yaml");
const MACHINE_ID: &str = "change_control";
const TERMINAL_STATES: &[&str] = &[
    "rejected",
    "deployed",
    "rolled_back",
    "rollback_failed_terminal",
];

type Host = CheckpointHost<InMemoryDefinitionResolver>;

#[derive(Clone)]
pub struct AppState {
    host: Arc<Host>,
    bundle: Arc<Bundle>,
    resolver: Arc<InMemoryDefinitionResolver>,
}

#[derive(Debug, Deserialize)]
pub struct CreateChange {
    pub change_id: String,
    pub creation_id: String,
    pub title: String,
}

#[derive(Debug, Deserialize)]
pub struct Command {
    pub operation_id: String,
    pub expected_revision: String,
    pub expected_checkpoint_digest: String,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub deployment_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ChangeView {
    pub change_id: String,
    pub revision: String,
    pub checkpoint_digest: String,
    pub status: String,
    pub state: Option<String>,
    pub variables: BTreeMap<String, JsonValue>,
    pub pending_delivery_count: usize,
    pub pending_outbox_count: usize,
    pub terminal_outbox_count: usize,
}

#[derive(Debug, Serialize)]
pub struct CommandResult {
    pub replayed: bool,
    pub change: ChangeView,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Role {
    Requester,
    Reviewer,
    Operator,
}

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl ApiError {
    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code: "bad_request",
            message: message.into(),
        }
    }

    fn forbidden(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            code: "forbidden",
            message: message.into(),
        }
    }

    fn not_found() -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            code: "change_not_found",
            message: "change does not exist".into(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({ "error": self.code, "message": self.message })),
        )
            .into_response()
    }
}

impl From<ArtifactError> for ApiError {
    fn from(value: ArtifactError) -> Self {
        let (status, code) = match value.code.as_str() {
            "checkpoint_not_found" => (StatusCode::NOT_FOUND, "change_not_found"),
            "checkpoint_conflict" => (StatusCode::CONFLICT, "revision_conflict"),
            "creation_id_conflict" => (StatusCode::CONFLICT, "creation_id_conflict"),
            "event_id_conflict" => (StatusCode::CONFLICT, "operation_id_conflict"),
            _ => (
                StatusCode::UNPROCESSABLE_ENTITY,
                "checkpoint_operation_failed",
            ),
        };
        Self {
            status,
            code,
            message: value.to_string(),
        }
    }
}

pub fn open_state(database_path: &FilePath) -> Result<AppState, Box<dyn std::error::Error>> {
    let bundle = Arc::new(load_bundle(MACHINE_SOURCE)?);
    let mut definitions = InMemoryDefinitionResolver::default();
    definitions.insert((*bundle).clone(), true);
    let resolver = Arc::new(definitions);
    let store = Arc::new(SqliteExecutionStore::open(
        database_path,
        DurableStoreMode::bounded(),
    )?);
    store.initialize_schema()?;
    store.health()?;
    let store: Arc<dyn ExecutionStore> = store;
    let host = Arc::new(CheckpointHost::new(store, resolver.clone()));
    Ok(AppState {
        host,
        bundle,
        resolver,
    })
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/changes", post(create_change))
        .route("/changes/{change_id}", get(get_change))
        .route("/changes/{change_id}/review", post(start_review))
        .route("/changes/{change_id}/approve", post(approve))
        .route("/changes/{change_id}/reject", post(reject))
        .route(
            "/changes/{change_id}/deployment/succeeded",
            post(deployment_succeeded),
        )
        .route(
            "/changes/{change_id}/deployment/failed",
            post(deployment_failed),
        )
        .route(
            "/changes/{change_id}/rollback/succeeded",
            post(rollback_succeeded),
        )
        .route(
            "/changes/{change_id}/rollback/failed",
            post(rollback_failed),
        )
        .route("/changes/{change_id}/outbox", get(get_outbox))
        .with_state(state)
}

async fn health() -> Json<JsonValue> {
    Json(
        json!({ "status": "ok", "service": "Determa State change control", "determa_state": "0.2.0" }),
    )
}

async fn create_change(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<CreateChange>,
) -> Result<(StatusCode, Json<CommandResult>), ApiError> {
    let actor = authorize(&headers, Role::Requester)?;
    require_nonempty(&input.change_id, "change_id")?;
    require_nonempty(&input.creation_id, "creation_id")?;
    require_nonempty(&input.title, "title")?;
    let before = state.host.load_checkpoint(&input.change_id)?;
    let bindings = Bindings {
        input: BTreeMap::from([
            ("change_id".into(), Value::String(input.change_id.clone())),
            ("title".into(), Value::String(input.title)),
            ("requester".into(), Value::String(actor)),
        ]),
        external: BTreeMap::new(),
    };
    state.host.create_checkpoint(
        &state.bundle,
        MACHINE_ID,
        &input.change_id,
        &input.creation_id,
        &bindings,
        None,
        json!({"mode":"bounded","permanent_replay_eligible":false,
            "pruned_through_receipt_sequence":null,"policy_identifier":"change-control-v1"}),
    )?;
    let change = inspect(&state, &input.change_id)?;
    Ok((
        if before.is_some() {
            StatusCode::OK
        } else {
            StatusCode::CREATED
        },
        Json(CommandResult {
            replayed: before.is_some(),
            change,
        }),
    ))
}

async fn get_change(
    State(state): State<AppState>,
    Path(change_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<ChangeView>, ApiError> {
    authorize_any(&headers)?;
    Ok(Json(inspect(&state, &change_id)?))
}

async fn get_outbox(
    State(state): State<AppState>,
    Path(change_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<JsonValue>, ApiError> {
    authorize(&headers, Role::Operator)?;
    let checkpoint = checkpoint(&state, &change_id)?;
    Ok(Json(json!({
        "revision": checkpoint.revision(),
        "checkpoint_digest": checkpoint.digest(),
        "pending": checkpoint.value()["pending_outbox_intents"],
        "terminal": checkpoint.value()["terminal_outbox_records"],
        "tombstones": checkpoint.value()["outbox_effect_tombstones"],
    })))
}

async fn start_review(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    let reviewer = authorize(&headers, Role::Reviewer)?;
    dispatch_command(
        &state,
        &id,
        "review_started",
        &command,
        BTreeMap::from([("reviewer".into(), Value::String(reviewer))]),
        None,
    )
    .map(Json)
}

async fn approve(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    let reviewer = authorize(&headers, Role::Reviewer)?;
    dispatch_command(
        &state,
        &id,
        "change_approved",
        &command,
        BTreeMap::from([("reviewer".into(), Value::String(reviewer))]),
        None,
    )
    .map(Json)
}

async fn reject(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    let reviewer = authorize(&headers, Role::Reviewer)?;
    let reason = required_option(&command.reason, "reason")?;
    dispatch_command(
        &state,
        &id,
        "change_rejected",
        &command,
        BTreeMap::from([
            ("reviewer".into(), Value::String(reviewer)),
            ("reason".into(), Value::String(reason)),
        ]),
        None,
    )
    .map(Json)
}

async fn deployment_succeeded(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    authorize(&headers, Role::Operator)?;
    let deployment_id = required_option(&command.deployment_id, "deployment_id")?;
    dispatch_command(
        &state,
        &id,
        "deployment_succeeded",
        &command,
        BTreeMap::from([("deployment_id".into(), Value::String(deployment_id))]),
        Some(id.clone()),
    )
    .map(Json)
}

async fn deployment_failed(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    authorize(&headers, Role::Operator)?;
    let reason = required_option(&command.reason, "reason")?;
    dispatch_command(
        &state,
        &id,
        "deployment_failed",
        &command,
        BTreeMap::from([("reason".into(), Value::String(reason))]),
        Some(id.clone()),
    )
    .map(Json)
}

async fn rollback_succeeded(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    authorize(&headers, Role::Operator)?;
    dispatch_command(
        &state,
        &id,
        "rollback_succeeded",
        &command,
        BTreeMap::new(),
        Some(id.clone()),
    )
    .map(Json)
}

async fn rollback_failed(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> Result<Json<CommandResult>, ApiError> {
    authorize(&headers, Role::Operator)?;
    let reason = required_option(&command.reason, "reason")?;
    dispatch_command(
        &state,
        &id,
        "rollback_failed",
        &command,
        BTreeMap::from([("reason".into(), Value::String(reason))]),
        Some(id.clone()),
    )
    .map(Json)
}

fn dispatch_command(
    state: &AppState,
    root: &str,
    event: &str,
    command: &Command,
    payload: BTreeMap<String, Value>,
    correlation_id: Option<String>,
) -> Result<CommandResult, ApiError> {
    require_nonempty(&command.operation_id, "operation_id")?;
    let before = checkpoint(state, root)?;
    let aggregate = &before.value()["root_record"]["aggregate_state"];
    let root_runtime = aggregate["runtimes"]
        .as_array()
        .ok_or_else(ApiError::not_found)?
        .iter()
        .find(|runtime| runtime["runtime_id"] == aggregate["root_runtime_id"])
        .ok_or_else(|| ApiError::bad_request("root runtime is absent"))?;
    let replayed = before.value()["operation_receipts"]
        .as_array()
        .unwrap()
        .iter()
        .any(|receipt| {
            receipt["operation_kind"] == "event_terminal"
                && receipt["event_id"] == command.operation_id
        });
    let terminal = root_runtime["active_leaf_state_definition_pointers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|pointer| {
            TERMINAL_STATES
                .iter()
                .any(|state| pointer.as_str().unwrap().ends_with(&format!("/{state}")))
        });
    if !replayed && (terminal || root_runtime["status"] != "running") {
        return Err(ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            code: "terminal_change",
            message: "completed or faulted changes do not accept commands".into(),
        });
    }
    let mut envelope = json!({
            "event": event,
            "event_id": command.operation_id,
            "cause_id": command.operation_id,
            "source": {"host":true},
            "target": root_runtime["target_identity"],
            "payload": TypedValue::from_value(&Value::Map(payload)),
    });
    if let Some(correlation_id) = correlation_id {
        envelope["correlation_id"] = json!(correlation_id);
    }
    use sha2::{Digest, Sha256};
    let operand = json!([
        "determa-inbox-envelope-digest-1",
        "1",
        root,
        "input",
        envelope
    ]);
    let digest = format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json_canonicalizer::to_vec(&operand)
                .map_err(|e| ApiError::bad_request(e.to_string()))?
        )
    );
    let candidate = json!({"delivery_mode":"input","envelope":envelope,"envelope_digest":digest});
    let receipt = state.host.process_checkpoint(
        root,
        &candidate,
        "foreground",
        &MutationGuard::new(
            &command.expected_revision,
            &command.expected_checkpoint_digest,
        ),
    )?;
    drop(receipt);
    Ok(CommandResult {
        replayed,
        change: inspect(state, root)?,
    })
}

fn inspect(state: &AppState, root: &str) -> Result<ChangeView, ApiError> {
    let checkpoint = checkpoint(state, root)?;
    let aggregate = &checkpoint.value()["root_record"]["aggregate_state"];
    if aggregate.is_null() {
        return Err(ApiError::not_found());
    }
    let restored = restore_aggregate(
        &serde_json_canonicalizer::to_vec(aggregate)
            .map_err(|e| ApiError::bad_request(e.to_string()))?,
        state.resolver.as_ref(),
    )
    .map_err(|e| ApiError::bad_request(e.to_string()))?;
    let configured_state = restored.root.config().into_iter().next();
    let variables: BTreeMap<String, JsonValue> = restored
        .root
        .variables
        .into_values()
        .map(|slot| (slot.name, value_to_json(slot.value)))
        .collect();
    let active_state = configured_state.or_else(|| {
        variables
            .get("lifecycle_status")
            .and_then(JsonValue::as_str)
            .map(str::to_owned)
    });
    let runtime_status = if active_state
        .as_deref()
        .is_some_and(|state| TERMINAL_STATES.contains(&state))
    {
        "completed".to_owned()
    } else {
        format!("{:?}", restored.root.status).to_lowercase()
    };
    Ok(ChangeView {
        change_id: root.to_owned(),
        revision: checkpoint.revision().to_owned(),
        checkpoint_digest: checkpoint.digest().to_owned(),
        status: runtime_status,
        state: active_state,
        variables,
        pending_delivery_count: aggregate["runtimes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| {
                r["ready_mailbox"].as_array().unwrap().len()
                    + r["deferred_mailbox"].as_array().unwrap().len()
            })
            .sum(),
        pending_outbox_count: checkpoint.value()["pending_outbox_intents"]
            .as_array()
            .unwrap()
            .len(),
        terminal_outbox_count: checkpoint.value()["terminal_outbox_records"]
            .as_array()
            .unwrap()
            .len(),
    })
}

fn checkpoint(state: &AppState, root: &str) -> Result<ExecutionCheckpoint, ApiError> {
    state
        .host
        .load_checkpoint(root)?
        .ok_or_else(ApiError::not_found)
}

fn value_to_json(value: Value) -> JsonValue {
    match value {
        Value::Null => JsonValue::Null,
        Value::Bool(value) => JsonValue::Bool(value),
        Value::Int(value) => json!(value),
        Value::Float(value) => json!(value),
        Value::String(value) => json!(value),
        Value::List(values) => JsonValue::Array(values.into_iter().map(value_to_json).collect()),
        Value::Map(values) => JsonValue::Object(
            values
                .into_iter()
                .map(|(key, value)| (key, value_to_json(value)))
                .collect(),
        ),
        Value::InstanceReference(value) => json!(value),
    }
}

fn authorize(headers: &HeaderMap, role: Role) -> Result<String, ApiError> {
    let actor = header(headers, "x-actor-id")?;
    let actual = header(headers, "x-actor-role")?;
    let expected = match role {
        Role::Requester => "requester",
        Role::Reviewer => "reviewer",
        Role::Operator => "operator",
    };
    if actual != expected {
        return Err(ApiError::forbidden(format!("{expected} role required")));
    }
    Ok(actor)
}

fn authorize_any(headers: &HeaderMap) -> Result<String, ApiError> {
    let actor = header(headers, "x-actor-id")?;
    match header(headers, "x-actor-role")?.as_str() {
        "requester" | "reviewer" | "operator" => Ok(actor),
        _ => Err(ApiError::forbidden("recognized actor role required")),
    }
}

fn header(headers: &HeaderMap, name: &'static str) -> Result<String, ApiError> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| ApiError::forbidden(format!("{name} header required")))
}

fn require_nonempty(value: &str, name: &'static str) -> Result<(), ApiError> {
    if value.is_empty() {
        Err(ApiError::bad_request(format!("{name} must not be empty")))
    } else {
        Ok(())
    }
}

fn required_option(value: &Option<String>, name: &'static str) -> Result<String, ApiError> {
    value
        .as_deref()
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| ApiError::bad_request(format!("{name} must not be empty")))
}
