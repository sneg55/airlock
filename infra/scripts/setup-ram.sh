#!/usr/bin/env bash
# Creates the two least-privilege RAM users (read, write) that back Airlock's
# capability separation, attaches infra/ram/{read,write}-policy.json, mints one
# AccessKey each, and configures local aliyun CLI profiles for them.
#
# Prerequisite: an authenticated aliyun CLI profile with RAM admin rights.
#   aliyun configure --mode OAuth --profile airlock
#
# Usage:
#   AUTH_PROFILE=airlock ./infra/scripts/setup-ram.sh
set -euo pipefail
set +x

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
auth_profile="${AUTH_PROFILE:-airlock}"
region="${REGION:-ap-southeast-1}"
read_user="${READ_USER_NAME:-airlock-read}"
write_user="${WRITE_USER_NAME:-airlock-write}"
read_policy_name="${READ_POLICY_NAME:-AirlockReadOnly}"
write_policy_name="${WRITE_POLICY_NAME:-AirlockWriteScoped}"

command -v aliyun >/dev/null 2>&1 || {
  printf 'aliyun CLI is required (brew install aliyun-cli)\n' >&2
  exit 1
}

aliyun configure list 2>/dev/null | grep -q "^${auth_profile}[[:space:]]" || {
  printf 'profile "%s" is not configured. Run: aliyun configure --mode OAuth --profile %s\n' \
    "$auth_profile" "$auth_profile" >&2
  exit 1
}

az() { aliyun "$@" --profile "$auth_profile"; }

ensure_policy() {
  local name="$1" doc_path="$2"
  if az ram GetPolicy --PolicyName "$name" --PolicyType Custom >/dev/null 2>&1; then
    printf 'policy %s already exists, skipping create\n' "$name"
    return
  fi
  az ram CreatePolicy \
    --PolicyName "$name" \
    --Description "Airlock capability-isolated policy ($name)" \
    --PolicyDocument "$(cat "$doc_path")" >/dev/null
  printf 'created policy %s from %s\n' "$name" "$doc_path"
}

ensure_user() {
  local name="$1"
  if az ram GetUser --UserName "$name" >/dev/null 2>&1; then
    printf 'user %s already exists, skipping create\n' "$name"
    return
  fi
  az ram CreateUser \
    --UserName "$name" \
    --DisplayName "$name" \
    --Comments "Airlock scoped credential, managed by infra/scripts/setup-ram.sh" >/dev/null
  printf 'created user %s\n' "$name"
}

ensure_attached() {
  local user="$1" policy="$2"
  az ram AttachPolicyToUser \
    --PolicyType Custom \
    --PolicyName "$policy" \
    --UserName "$user" >/dev/null 2>&1 || true
  printf 'attached policy %s to user %s\n' "$policy" "$user"
}

# CreateAccessKey returns the secret exactly once. Never echo it; only ever
# write it straight into the local, gitignored aliyun CLI profile store.
ensure_local_profile() {
  local user="$1" cli_profile="$2"
  if aliyun configure list 2>/dev/null | grep -q "^${cli_profile}[[:space:]]"; then
    printf 'local profile %s already configured, leaving it as-is (rotate manually if needed)\n' \
      "$cli_profile"
    return
  fi
  local existing_keys
  existing_keys="$(az ram ListAccessKeys --UserName "$user" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("AccessKeys",{}).get("AccessKey",[])))')"
  if [[ "$existing_keys" -ge 2 ]]; then
    printf 'user %s already has 2 AccessKeys (the max). Rotate manually in the RAM console.\n' \
      "$user" >&2
    return
  fi
  local created ak_id ak_secret
  created="$(az ram CreateAccessKey --UserName "$user")"
  ak_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])' <<<"$created")"
  ak_secret="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["AccessKey"]["AccessKeySecret"])' <<<"$created")"
  aliyun configure set \
    --profile "$cli_profile" \
    --mode AK \
    --access-key-id "$ak_id" \
    --access-key-secret "$ak_secret" \
    --region "$region" \
    --language en >/dev/null
  unset ak_secret created
  printf 'configured local CLI profile "%s" for user %s (secret stored only in ~/.aliyun/config.json)\n' \
    "$cli_profile" "$user"
}

ensure_policy "$read_policy_name" "$repo_root/infra/ram/read-policy.json"
ensure_policy "$write_policy_name" "$repo_root/infra/ram/write-policy.json"
ensure_user "$read_user"
ensure_user "$write_user"
ensure_attached "$read_user" "$read_policy_name"
ensure_attached "$write_user" "$write_policy_name"
ensure_local_profile "$read_user" "airlock-read"
ensure_local_profile "$write_user" "airlock-write"

cat <<EOF

Done. Verify with:
  aliyun ecs DescribeRegions --profile airlock-read
  aliyun ecs DescribeInstances --profile airlock-write   # should be denied (write policy has no Describe)

Next: point the read and write MCP server deployments at these profiles
(infra/deploy/mcp-read-fc.sh, infra/deploy/mcp-write-fc.sh), or prefer
EcsRamRole for the deployed servers instead of these long-lived keys, per
infra/README.md.
EOF
