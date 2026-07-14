#!/usr/bin/env bash
set -euo pipefail
set +x

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
key_dir="$repo_root/.airlock"
private_key="$key_dir/signing.key"
public_key="$key_dir/executor-public.pem"

command -v openssl >/dev/null 2>&1 || {
  printf 'openssl is required\n' >&2
  exit 1
}

if [[ -e "$private_key" || -e "$public_key" ]]; then
  printf 'refusing to overwrite an existing key. Remove both key files to rotate.\n' >&2
  exit 1
fi

umask 077
mkdir -p "$key_dir"
openssl genpkey -algorithm ED25519 -out "$private_key"
openssl pkey -in "$private_key" -pubout -out "$public_key"
chmod 600 "$private_key"
chmod 644 "$public_key"

fingerprint="$(openssl pkey -pubin -in "$public_key" -outform DER |
  openssl dgst -sha256 | awk '{print $NF}')"
key_id="airlock-${fingerprint:0:16}"

printf 'generated checkpoint private key: %s\n' "$private_key"
printf 'generated executor public key: %s\n' "$public_key"
printf 'set KEY_ID=%s on checkpoint and executor\n' "$key_id"
