# Airlock

[![License](https://img.shields.io/github/license/sneg55/airlock)](LICENSE)
[![Stars](https://img.shields.io/github/stars/sneg55/airlock)](https://github.com/sneg55/airlock/stargazers)
![Python 3.13+](https://img.shields.io/badge/Python%203.13%2B-3776AB?logo=python&logoColor=fff)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=fff)
![Alibaba Cloud](https://img.shields.io/badge/Alibaba%20Cloud-FF6A00?logo=alibabacloud&logoColor=fff)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=fff)

**Safe autonomy for destructive cloud actions.** Airlock is an agent that finds wasteful
cloud resources and cleans them up, but the point is not the cleanup. The point is the gate:
every destructive action passes through a human approval the agent structurally cannot
bypass or re-target.

Approval buttons in most agent workflows are theater. The agent proposes "delete resource
X," a human clicks approve, and nothing binds that approval to X. The agent can then execute
a different action, or hit a different target, and the click still counts. Airlock closes
that gap: an approval is an Ed25519 signature over the exact action, single-use and
time-limited. Change any field and the signature no longer matches, so the write is refused.

Today Airlock targets Alibaba Cloud (ECS + RDS). The gate itself is provider-agnostic; see
[CONTRIBUTING.md](CONTRIBUTING.md) for adding a provider.

## The guarantee, tested (no cloud account)

The approval kernel enforces the core property: an approved action executes, a replay is
refused, and a swapped target is refused. These behaviors are covered end to end by the test
and conformance suites, so you can verify the guarantee without a cloud account.

```bash
uv sync --dev
uv run pytest                      # unit, integration, and property tests
```

Conformance vectors for the shared canonicalizer (signer and verifier) live in `eval/`.

## Run it for real (Alibaba Cloud)

1. `bash infra/scripts/gen-keys.sh` generates the Ed25519 keypair into `.airlock/`.
2. `bash infra/scripts/setup-ram.sh` creates two RAM users: a read-only one and a
   write-only one. This separation is the enforcement boundary.
3. Copy `.env.example` to `.env` and fill it in; put the RAM creds in `.airlock/read.env`
   and `.airlock/write.env`.
4. `docker compose up --build` brings up the checkpoint, the read-only agent, the
   write-only executor, the two credentialed MCP servers, and the web approval app.

Review and approve proposals at `http://localhost:3000`.

## Architecture

Four components, each with the least capability it needs. Run them as separate processes or
containers so the split is a deployment boundary, not just a convention. See
[docs/architecture.md](docs/architecture.md) for the full diagram and trust boundaries.

| Component (`airlock ...`) | Capability | Role |
|---|---|---|
| `agent` | read-only cloud | Discovers idle instances, applies the idle policy, submits a proposal. Never writes. |
| `checkpoint` | signing key | Hashes the proposal (RFC 8785 JCS) and issues an Ed25519 approval bound to that hash. The only signer. |
| `executor` | write cloud | Re-verifies the approval, re-reads live state, rebuilds args from trusted fields, writes once. The only writer. |
| web app | operator token | The human review surface; type-to-confirm on high-risk actions. |

- **Reads and metrics** go through a self-hosted [`alibaba-cloud-ops-mcp-server`](https://github.com/aliyun/alibaba-cloud-ops-mcp-server)
  under the read RAM credential: `CommonAPICaller` for instance discovery and
  `CMS_GetMetricList` for a windowed CPU/mem/disk time series (a small vendored tool; see
  `infra/mcp/`).
- **Writes** go through a second MCP server under the write RAM credential, exposing only
  `ECS_StopInstances`, `ECS_DeleteInstances`, `RDS_StopDBInstance`.
- **Planning** is deterministic by default: the idle policy makes the judgment and the
  planner just formats the canonical action (no API key, cannot hallucinate a target). An
  optional Qwen arbiter (`QwenPlanner`, DashScope chat-completions) can be layered on.

## How the gate works

1. The agent submits a `ProposedAction` (`{account, region, resource_id, action,
   precondition, evidence, ...}`).
2. The checkpoint canonicalizes it (JCS), hashes it, and on operator approval signs an
   envelope binding the hash, account, audience, key id, a nonce, and an expiry.
3. The executor, before any write: verifies the signature, re-hashes the presented action
   and compares, checks account/audience/key/expiry, atomically consumes the approval
   (single use), re-reads live state against the precondition, and only then calls the write
   MCP tool with arguments reconstructed from trusted fields.

The signer and verifier share one canonicalizer; conformance vectors live in `eval/`.

## The `airlock` command

```
airlock checkpoint [--host --port]      serve the approval checkpoint
airlock agent [--loop]                  one read-only discovery + proposal pass
airlock executor [--once]               the write-only executor daemon
airlock approvals list|approve|reject   operator surface
```

## Configuration

All settings are environment variables read in one place (`src/common/env.py`); see
`.env.example`. `CLOUD_REGION` is a single knob (defaults to `ap-southeast-1`); if you change
it, update the RAM policy ARNs in `infra/ram/` to match.

## Status and limits

Airlock is early and honest about its edges. The approval kernel is well-tested (`tests/` and
`eval/`); the surrounding product is maturing. Notable current limits: Alibaba Cloud only;
operator auth is a single shared bearer (no per-user auth yet); the signing key is a local
file by default (use a KMS in production); the approval store is SQLite. Read
[SECURITY.md](SECURITY.md) before trusting it with a real account.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The one rule that never bends: the planner never
holds a write capability or the signing key, and the executor never holds the private key.

## License

MIT. See [LICENSE](LICENSE).
