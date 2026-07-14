#!/usr/bin/env bash
set -euo pipefail
set +x

# The current checkpoint store is SQLite. It is process-local coordination backed by one
# database file, so this deployment is deliberately pinned to one Uvicorn worker. Moving
# to RDS or Redis requires an ApprovalStore implementation with a database-atomic claim.

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'required environment variable is unset: %s\n' "$name" >&2
    exit 1
  fi
}

reject_newline() {
  local name="$1"
  if [[ "${!name}" == *$'\n'* || "${!name}" == *$'\r'* ]]; then
    printf 'environment variable contains a newline: %s\n' "$name" >&2
    exit 1
  fi
}

for name in CHECKPOINT_ECS_HOST CHECKPOINT_SSH_USER CHECKPOINT_CONTAINER_IMAGE \
  OPERATOR_TOKEN OPERATOR_IDENTITY SIGNING_KEY_PATH KEY_ID APPROVAL_STORE_PATH \
  BOUND_ACCOUNT_ID CHECKPOINT_URL PORT APP_ENV LOG_LEVEL; do
  require "$name"
  reject_newline "$name"
done

AIRLOCK_REGION="${AIRLOCK_REGION:-ap-southeast-1}"  # configurable; match RAM ARNs and creds to it

if [[ "$CHECKPOINT_URL" != https://* ]]; then
  printf 'CHECKPOINT_URL must use TLS\n' >&2
  exit 1
fi

if [[ ! -r "$SIGNING_KEY_PATH" ]]; then
  printf 'SIGNING_KEY_PATH is not readable: %s\n' "$SIGNING_KEY_PATH" >&2
  exit 1
fi

if [[ "$APPROVAL_STORE_PATH" != /var/lib/airlock/* ]]; then
  printf 'APPROVAL_STORE_PATH must be under /var/lib/airlock\n' >&2
  exit 1
fi

target="${CHECKPOINT_SSH_USER}@${CHECKPOINT_ECS_HOST}"
remote_root="/opt/airlock"

# Client-side expansion is intentional. These values select fixed remote paths.
# shellcheck disable=SC2029
ssh "$target" "sudo install -d -m 700 '$remote_root/secrets' '$remote_root/config' && sudo install -d -m 700 /var/lib/airlock"

# shellcheck disable=SC2029
printf '%s\n' \
  "APP_ENV=$APP_ENV" \
  "PORT=$PORT" \
  "LOG_LEVEL=$LOG_LEVEL" \
  "OPERATOR_TOKEN=$OPERATOR_TOKEN" \
  "OPERATOR_IDENTITY=$OPERATOR_IDENTITY" \
  "SIGNING_KEY_PATH=/run/secrets/signing.key" \
  "KEY_ID=$KEY_ID" \
  "APPROVAL_STORE_PATH=$APPROVAL_STORE_PATH" \
  "BOUND_ACCOUNT_ID=$BOUND_ACCOUNT_ID" \
  "CHECKPOINT_URL=$CHECKPOINT_URL" |
  ssh "$target" "sudo install -m 600 /dev/stdin '$remote_root/config/checkpoint.env'"

# shellcheck disable=SC2029
ssh "$target" "sudo install -m 600 /dev/stdin '$remote_root/secrets/signing.key'" \
  < "$SIGNING_KEY_PATH"

# shellcheck disable=SC2029
ssh "$target" sudo docker pull "$CHECKPOINT_CONTAINER_IMAGE"
ssh "$target" sudo docker rm -f airlock-checkpoint >/dev/null 2>&1 || true
ssh "$target" sudo docker run -d \
  --name airlock-checkpoint \
  --restart unless-stopped \
  --env-file "$remote_root/config/checkpoint.env" \
  -p "127.0.0.1:${PORT}:${PORT}" \
  -v /var/lib/airlock:/var/lib/airlock \
  -v "$remote_root/secrets/signing.key:/run/secrets/signing.key:ro" \
  "$CHECKPOINT_CONTAINER_IMAGE" \
  uvicorn src.checkpoint.service:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1 >/dev/null

printf 'checkpoint deployed with one Uvicorn worker and SQLite at %s\n' \
  "$APPROVAL_STORE_PATH"
