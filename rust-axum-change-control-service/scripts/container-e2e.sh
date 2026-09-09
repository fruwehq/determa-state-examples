#!/usr/bin/env bash
set -euo pipefail

image="determa-change-control-example:e2e"
container="determa-change-control-e2e-$$"
volume="determa-change-control-e2e-$$"
port="${CHANGE_CONTROL_E2E_PORT:-18085}"

cleanup() {
  docker rm -f "${container}" >/dev/null 2>&1 || true
  docker volume rm "${volume}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_health() {
  for _ in $(seq 1 30); do
    if test "$(docker inspect --format '{{.State.Health.Status}}' "${container}")" = "healthy"; then
      curl --fail --silent "http://127.0.0.1:${port}/health" >/dev/null
      return
    fi
    sleep 1
  done
  docker logs "${container}"
  return 1
}

docker build -t "${image}" .
docker volume create "${volume}" >/dev/null
docker run -d --name "${container}" -p "${port}:8080" -v "${volume}:/data" "${image}" >/dev/null
wait_for_health

create_response="$(curl --fail --silent -X POST "http://127.0.0.1:${port}/changes" \
  -H 'content-type: application/json' \
  -H 'x-actor-id: alice' -H 'x-actor-role: requester' \
  -d '{"change_id":"container-change","creation_id":"container-create","title":"Rotate production signing key"}')"
revision="$(printf '%s' "${create_response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["change"]["revision"])')"
digest="$(printf '%s' "${create_response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["change"]["checkpoint_digest"])')"

review_response="$(curl --fail --silent -X POST "http://127.0.0.1:${port}/changes/container-change/review" \
  -H 'content-type: application/json' \
  -H 'x-actor-id: bob' -H 'x-actor-role: reviewer' \
  -d "{\"operation_id\":\"container-review\",\"expected_revision\":\"${revision}\",\"expected_checkpoint_digest\":\"${digest}\"}")"
revision="$(printf '%s' "${review_response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["change"]["revision"])')"
digest="$(printf '%s' "${review_response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["change"]["checkpoint_digest"])')"

approve_response="$(curl --fail --silent -X POST "http://127.0.0.1:${port}/changes/container-change/approve" \
  -H 'content-type: application/json' \
  -H 'x-actor-id: bob' -H 'x-actor-role: reviewer' \
  -d "{\"operation_id\":\"container-approve\",\"expected_revision\":\"${revision}\",\"expected_checkpoint_digest\":\"${digest}\"}")"
printf '%s' "${approve_response}" | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["change"]["state"] == "deploying"; assert value["change"]["pending_outbox_count"] == 1'

outbox="$(curl --fail --silent "http://127.0.0.1:${port}/changes/container-change/outbox" \
  -H 'x-actor-id: release-bot' -H 'x-actor-role: operator')"
printf '%s' "${outbox}" | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["pending"][0]["intent"]["event"] == "deployment_requested"'

docker stop "${container}" >/dev/null
docker rm "${container}" >/dev/null
docker run -d --name "${container}" -p "${port}:8080" -v "${volume}:/data" "${image}" >/dev/null
wait_for_health
restored="$(curl --fail --silent "http://127.0.0.1:${port}/changes/container-change" \
  -H 'x-actor-id: alice' -H 'x-actor-role: requester')"
printf '%s' "${restored}" | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["state"] == "deploying"; assert value["pending_outbox_count"] == 1'

echo "Container health, workflow, outbox, and restart checks passed."
