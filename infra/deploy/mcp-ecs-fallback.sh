#!/usr/bin/env bash
set -euo pipefail
set +x

# ECS fallback for legacy SSE when Function Compute session recycling is unreliable.
# Both containers bind to loopback. A TLS gateway must expose the read URL as needed and
# expose the write URL only to the executor while enforcing WRITE_MCP_BEARER.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
read_tools="$(tr -d '\r\n' < "$repo_root/infra/mcp/read-visible-tools.txt")"
write_tools="$(tr -d '\r\n' < "$repo_root/infra/mcp/write-visible-tools.txt")"

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

for name in MCP_ECS_HOST MCP_ECS_SSH_USER MCP_CONTAINER_IMAGE MCP_READ_ACCESS_KEY_ID \
  MCP_READ_ACCESS_KEY_SECRET MCP_WRITE_ACCESS_KEY_ID MCP_WRITE_ACCESS_KEY_SECRET \
  READ_MCP_SSE_URL WRITE_MCP_SSE_URL WRITE_MCP_BEARER BOUND_ACCOUNT_ID; do
  require "$name"
  reject_newline "$name"
done

AIRLOCK_REGION="${AIRLOCK_REGION:-ap-southeast-1}"  # configurable; match RAM ARNs and creds to it

if [[ "$READ_MCP_SSE_URL" != https://* || "$WRITE_MCP_SSE_URL" != https://* ]]; then
  printf 'both MCP URLs must use TLS\n' >&2
  exit 1
fi

read_port="${MCP_READ_PORT:-8001}"
write_port="${MCP_WRITE_PORT:-8002}"
target="${MCP_ECS_SSH_USER}@${MCP_ECS_HOST}"
remote_root="/opt/airlock-mcp"

# Client-side expansion is intentional. These values select fixed remote paths.
# shellcheck disable=SC2029
ssh "$target" "sudo install -d -m 700 '$remote_root'"

# shellcheck disable=SC2029
printf '%s\n' \
  "ALIBABA_CLOUD_ACCESS_KEY_ID=$MCP_READ_ACCESS_KEY_ID" \
  "ALIBABA_CLOUD_ACCESS_KEY_SECRET=$MCP_READ_ACCESS_KEY_SECRET" |
  ssh "$target" "sudo install -m 600 /dev/stdin '$remote_root/read.env'"

# shellcheck disable=SC2029
printf '%s\n' \
  "ALIBABA_CLOUD_ACCESS_KEY_ID=$MCP_WRITE_ACCESS_KEY_ID" \
  "ALIBABA_CLOUD_ACCESS_KEY_SECRET=$MCP_WRITE_ACCESS_KEY_SECRET" \
  "AIRLOCK_MCP_BEARER=$WRITE_MCP_BEARER" |
  ssh "$target" "sudo install -m 600 /dev/stdin '$remote_root/write.env'"

# shellcheck disable=SC2029
ssh "$target" sudo docker pull "$MCP_CONTAINER_IMAGE"
ssh "$target" sudo docker rm -f airlock-mcp-read airlock-mcp-write >/dev/null 2>&1 || true

ssh "$target" sudo docker run -d \
  --name airlock-mcp-read \
  --restart unless-stopped \
  --env-file "$remote_root/read.env" \
  -p "127.0.0.1:${read_port}:8000" \
  "$MCP_CONTAINER_IMAGE" \
  --transport sse \
  --host 0.0.0.0 \
  --port 8000 \
  --env international \
  --services ecs,cms \
  --visible-tools "$read_tools" >/dev/null

ssh "$target" sudo docker run -d \
  --name airlock-mcp-write \
  --restart unless-stopped \
  --env-file "$remote_root/write.env" \
  -p "127.0.0.1:${write_port}:8000" \
  "$MCP_CONTAINER_IMAGE" \
  --transport sse \
  --host 0.0.0.0 \
  --port 8000 \
  --env international \
  --extra-config "{'ecs': ['StopInstances', 'DeleteInstances'], 'rds': ['StopDBInstance']}" \
  --visible-tools "$write_tools" >/dev/null

printf 'ECS fallback deployed. Complete TLS gateway and security-group checks before use.\n'
