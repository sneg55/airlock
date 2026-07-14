#!/usr/bin/env bash
set -euo pipefail
set +x

# Function Compute SSE needs a custom container, a 0.0.0.0 bind, chunked streaming,
# and a long timeout. Legacy SSE sessions must tolerate instance recycling.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
template="$repo_root/infra/deploy/mcp-read-fc.yaml"
allowlist_file="$repo_root/infra/mcp/read-visible-tools.txt"

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'required environment variable is unset: %s\n' "$name" >&2
    exit 1
  fi
}

export AIRLOCK_REGION="${AIRLOCK_REGION:-ap-southeast-1}"
export SERVERLESS_DEVS_ACCESS="${SERVERLESS_DEVS_ACCESS:-default}"
export FC_READ_FUNCTION_NAME="${FC_READ_FUNCTION_NAME:-airlock-mcp-read}"

# AIRLOCK_REGION is configurable (defaults to ap-southeast-1, set above). If you change it,
# update the RAM policy ARNs in infra/ram/ and the MCP credentials to match that region.

for name in FC_EXECUTION_ROLE_ARN MCP_CONTAINER_IMAGE MCP_READ_ACCESS_KEY_ID \
  MCP_READ_ACCESS_KEY_SECRET READ_MCP_SSE_URL BOUND_ACCOUNT_ID; do
  require "$name"
done

if [[ "$READ_MCP_SSE_URL" != https://* ]]; then
  printf 'READ_MCP_SSE_URL must use TLS\n' >&2
  exit 1
fi

command -v s >/dev/null 2>&1 || {
  printf 'Serverless Devs command s is required\n' >&2
  exit 1
}

export MCP_VISIBLE_TOOLS
MCP_VISIBLE_TOOLS="$(tr -d '\r\n' < "$allowlist_file")"

# MCP_CONTAINER_IMAGE must be an ap-southeast-1 ACR image that pins
# alibaba-cloud-ops-mcp-server 0.9.27 and preserves streaming responses.
s deploy -t "$template" -y

printf 'read MCP deployed. Configure READ_MCP_SSE_URL=%s/sse\n' \
  "${READ_MCP_SSE_URL%/sse}"
