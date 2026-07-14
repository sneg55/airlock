#!/usr/bin/env bash
set -euo pipefail
set +x

# Function Compute SSE needs a custom container, a 0.0.0.0 bind, chunked streaming,
# and a long timeout. The write URL stays private and is reached through a TLS gateway
# that validates WRITE_MCP_BEARER before forwarding to the function.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
template="$repo_root/infra/deploy/mcp-write-fc.yaml"
allowlist_file="$repo_root/infra/mcp/write-visible-tools.txt"

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'required environment variable is unset: %s\n' "$name" >&2
    exit 1
  fi
}

export AIRLOCK_REGION="${AIRLOCK_REGION:-ap-southeast-1}"
export SERVERLESS_DEVS_ACCESS="${SERVERLESS_DEVS_ACCESS:-default}"
export FC_WRITE_FUNCTION_NAME="${FC_WRITE_FUNCTION_NAME:-airlock-mcp-write}"

# AIRLOCK_REGION is configurable (defaults to ap-southeast-1, set above). If you change it,
# update the RAM policy ARNs in infra/ram/ and the MCP credentials to match that region.

for name in FC_EXECUTION_ROLE_ARN MCP_CONTAINER_IMAGE MCP_WRITE_ACCESS_KEY_ID \
  MCP_WRITE_ACCESS_KEY_SECRET WRITE_MCP_SSE_URL WRITE_MCP_BEARER BOUND_ACCOUNT_ID; do
  require "$name"
done

if [[ "$WRITE_MCP_SSE_URL" != https://* ]]; then
  printf 'WRITE_MCP_SSE_URL must use TLS\n' >&2
  exit 1
fi

command -v s >/dev/null 2>&1 || {
  printf 'Serverless Devs command s is required\n' >&2
  exit 1
}

export MCP_VISIBLE_TOOLS
MCP_VISIBLE_TOOLS="$(tr -d '\r\n' < "$allowlist_file")"

# The image or private gateway must consume AIRLOCK_MCP_BEARER. The upstream MCP
# package alone does not enforce bearer authentication.
s deploy -t "$template" -y

printf 'write MCP deployed behind the configured private TLS gateway\n'
