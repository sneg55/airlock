# Airlock deployment runbook

Following this runbook stands up Airlock on Alibaba Cloud in the configured region
(`AIRLOCK_REGION`, default `ap-southeast-1`). Function Compute is the primary MCP target. A
small ECS host is the fallback when legacy SSE session behavior on Function Compute is
unreliable. The scripts make no cloud calls at repository-validation time; they run only when
you invoke them with real credentials.

## Security boundaries

Create two unrelated RAM identities. Attach `ram/read-policy.json` to the read identity and
`ram/write-policy.json` to the write identity. Never attach both policies to one identity.
The planner receives only `READ_MCP_SSE_URL` and the read MCP credential has no mutate
action. The executor alone receives `WRITE_MCP_SSE_URL` and `WRITE_MCP_BEARER`.

The write MCP endpoint must not be internet-open. Put it behind an internal Function Compute
custom domain, API gateway, or ECS reverse proxy whose security group admits only the
executor. Terminate TLS there and require the bearer value from `WRITE_MCP_BEARER`. The
upstream MCP package does not provide this Airlock bearer boundary by itself, so
`MCP_CONTAINER_IMAGE` must include a narrow authenticating proxy or the gateway must enforce
it. Store both MCP AccessKey pairs and the bearer in Alibaba Cloud Secret Manager or KMS.
Export them only for the deployment command, disable shell tracing, and rotate them after
any suspected exposure.

## Required runtime configuration

Airlock application settings use the exact names loaded by `src/common/env.py`:

- Checkpoint: `APP_ENV`, `PORT`, `LOG_LEVEL`, `OPERATOR_TOKEN`, `OPERATOR_IDENTITY`,
  `SIGNING_KEY_PATH`, `KEY_ID`, `APPROVAL_STORE_PATH`, `BOUND_ACCOUNT_ID`, and
  `CHECKPOINT_URL`.
- Planner and executor integration: `QWEN_API_KEY`, `QWEN_BASE_URL`,
  `QWEN_PLANNER_MODEL`, `JURY_MODELS`, `JURY_DISAGREEMENT_THRESHOLD`,
  `MEMORY_MAX_RECEIPT_AGE_SECONDS`, `READ_MCP_SSE_URL`, `WRITE_MCP_SSE_URL`, and
  `WRITE_MCP_BEARER`.

Infrastructure scripts also require the deployment variables named in each script. Use an
ACR image in `ap-southeast-1` for `MCP_CONTAINER_IMAGE`. Pin that image to
`alibaba-cloud-ops-mcp-server` release `0.9.27`, preserve MCP SSE streaming, and add bearer
enforcement for the write deployment. Serverless Devs must be configured through
`SERVERLESS_DEVS_ACCESS` without placing its credential in this repository.

## Deploy order

1. Generate keys with `infra/scripts/gen-keys.sh`. It writes
   `.airlock/signing.key` and `.airlock/executor-public.pem`, then prints the `KEY_ID` value.
   Put only the private key in the checkpoint secret store. Put only the public key and the
   same `KEY_ID` in the executor environment. The planner gets neither key.
2. Authenticate an admin CLI profile once with `aliyun configure --mode OAuth --profile
   airlock` (interactive, in your own terminal; not scriptable, this proves it is you). Then
   run `AUTH_PROFILE=airlock infra/scripts/setup-ram.sh` to create the `airlock-read` and
   `airlock-write` RAM users, attach `ram/read-policy.json` and `ram/write-policy.json`
   respectively, mint one AccessKey per user, and store each key only in a local
   `airlock-read` / `airlock-write` aliyun CLI profile (never printed, never committed). Move
   each key into its own Secret Manager/KMS entry before deploying; the local profile is for
   verification only. Verify the read identity is denied for every action in the write
   policy: `aliyun ecs DeleteInstances --InstanceId i-xxx --profile airlock-read` must fail.
3. Build and push the pinned MCP container image to ACR in the configured region. Confirm the image
   advertises only the names in `mcp/read-visible-tools.txt` or
   `mcp/write-visible-tools.txt` when started with the corresponding `--visible-tools`
   value.
4. Export the read deployment values and run `infra/deploy/mcp-read-fc.sh`. Set the resulting
   TLS SSE endpoint as `READ_MCP_SSE_URL`.
5. Export the separate write values and run `infra/deploy/mcp-write-fc.sh`. Complete the
   private TLS gateway and executor-only network route before setting `WRITE_MCP_SSE_URL`.
   If FC SSE is unstable, run `infra/deploy/mcp-ecs-fallback.sh` on an ECS host in the same
   region and retain the same credential and network separation.
6. Run `infra/deploy/checkpoint.sh` against an ECS host in the same region. It transfers the private
   key without printing it, binds the container to loopback, persists SQLite on the ECS
   disk, and starts exactly `uvicorn --workers 1`. Place an operator-facing TLS proxy in
   front of it. The approve and reject path must not be reachable by planner or orchestrator
   processes.
7. Configure the planner, executor, and approval UI. The planner gets the read URL only. The
   executor gets the write URL, write bearer, and public verification key only. The
   checkpoint gets the operator bearer and private signing key only.

## Approval store choice

The committed checkpoint service currently implements SQLite. Its atomic claim is safe for
the deployed proof only when one process owns the database, so `checkpoint.sh` fixes Uvicorn
at one worker and one container. Do not scale that deployment horizontally.

For production scale, implement the existing `ApprovalStore` protocol on RDS or Redis with
a database-atomic compare-and-set claim, migrate the records, and only then add workers or
replicas. An RDS transaction can update an issued, unexpired row to consumed with a status
predicate and require exactly one affected row. A Redis implementation can use a Lua script
or an equivalent atomic primitive. That implementation is outside Cycle E because this
cycle cannot change Python source.

## Live integration verification

Complete every item with a throwaway account and throwaway resources before a demo:

- Confirm the read RAM identity can call only `ecs:DescribeInstances` and
  `cms:DescribeMetricLast`, and receives an explicit authorization denial for
  `ecs:StopInstances`, `ecs:DeleteInstances`, and `rds:StopDBInstance`.
- Confirm the write identity can affect only ECS and RDS instances in `ap-southeast-1` and
  cannot list unrelated resources. Verify whether the pinned MCP implementation routes stop
  operations through OOS. If it does, replace its batch OOS adapter with a direct,
  single-target adapter or revise the narrowly scoped policy only after reviewing every
  additional API permission.
- Compare the deployed tool catalog with both allowlist files. Current Airlock code calls
  `StopDBInstances`, while upstream `0.9.27` may advertise `StopRDSInstances`. Resolve that
  mismatch in the deployment adapter without broadening the executor action enum.
- Validate request arguments and responses for `DescribeInstances`, `GetCpuUsageData`,
  `GetMemUsedData`, and `GetDiskUsageData`. Airlock currently sends `InstanceId`,
  `StartTime`, and `EndTime`, while upstream monitoring tools may expect `InstanceIds` and
  return only the latest CMS datapoint. Confirm the actual `DescribeInstances` and
  `Get*UsageData` payload shapes, timestamps, tag layout, and metric values before trusting
  idle evidence.
- Validate Function Compute SSE chunked streaming, `0.0.0.0` binding, the `/sse` and message
  endpoints, long keep-alive behavior, and session behavior across instance recycling. Run
  the ECS fallback if sessions cannot remain correct for the recorded workflow.
- Confirm the write URL is unreachable outside the executor security group, rejects a
  missing or incorrect bearer, accepts the correct bearer over TLS, and never logs the
  bearer or RAM credentials.
- Confirm checkpoint approve and reject routes require `OPERATOR_TOKEN`, are unreachable
  from planner and orchestrator networks, and persist across a container restart. Attempt
  two concurrent claims and confirm only one succeeds.
- Exercise stop and delete only on throwaway resources. Verify the live state through the
  read MCP server before and after each separately approved action.
- Test the live Qwen planner and jury. `QwenPlanner` and the multi-model jury call the
  OpenAI-compatible chat-completions endpoint with `response_format={"type": "json_object"}`
  (not the Responses API and not `json_schema` strict output). Confirm every configured model
  (`QWEN_PLANNER_MODEL` and each of `JURY_MODELS`) is enabled on the key and returns parseable
  JSON. `plan_action` re-validates the output and the executor re-checks trusted fields
  regardless, so a malformed or adversarial completion cannot widen the action.

## Steps that require a live Alibaba Cloud account

- Create and attach the two RAM identities and policies, then prove direct denials.
- Create ACR repositories, build and push the pinned deployment image.
- Deploy the two Function Compute functions, their HTTP triggers, private networking, TLS
  domains or gateway, and secret delivery.
- Provision the ECS fallback and checkpoint host, security groups, persistent disk, and TLS
  proxy. Provision RDS or Redis only after a database-backed store implementation exists.
- Run the Qwen chat-completions planner and jury, FC SSE, MCP payload, bearer isolation, and throwaway resource
  stop and delete verification.
