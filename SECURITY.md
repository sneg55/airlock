# Security policy

Airlock executes destructive cloud actions (stopping and deleting resources), so its
security model is the product. This document states what Airlock guarantees, the threats it
is designed to resist, its current limits, and how to report a vulnerability.

## The core guarantee

Every write is gated by a human approval that is cryptographically bound to one exact
action. The guarantee is enforced by capability separation, not by prompt filtering:

- The **planner/agent** has read-only cloud capability. It can propose an action; it holds
  no write credential and no signing key.
- The **checkpoint** is the only component that can sign. It hashes the proposed action
  (RFC 8785 JCS canonicalization) and issues an Ed25519-signed, single-use, time-limited
  approval bound to that hash and to the target account, audience, and key id.
- The **executor** is the only write-capable component. Before any write it re-verifies the
  signature, re-hashes the presented action, reconstructs write arguments from trusted
  fields (never a free-form blob), re-reads live state, and atomically consumes the approval.

Change any field after sign-off and the hash differs, so execution is refused. An approval
authorizes exactly one action against exactly one target, once.

## Threats in scope (and how they are resisted)

- **Agent retargets after approval** → the executor re-hashes; a changed target or action
  fails the hash check (`E_VERIFY_006`).
- **Approval replay** → single-use atomic claim; a second use is refused (`E_VERIFY_009`).
- **Stale approval** → time-limited expiry (`E_VERIFY_*` expired).
- **Wrong tenant / cross-account** → account binding is checked against the bound account.
- **Key confusion** → the approval binds a key id and audience; mismatches are refused.
- **State drift between approval and execution** → mandatory pre-write re-read of live state
  against the recorded precondition.
- **Planner is given a write tool** → the planner's MCP client is read-credentialed only;
  the write MCP server runs under a separate RAM credential and bearer the planner never has.

## Current limits (do not assume these are solved)

- **Operator authentication** is a single shared bearer token with an identity string. There
  is no per-user auth, MFA, or tamper-proof audit log yet. Put the checkpoint and web app
  behind your own SSO/network isolation for production use.
- **Signing key storage** is a local file by default. For production, hold the private key
  in a KMS/HSM and give the executor only the public key.
- **Approval store** is SQLite (single-node). No HA or backup story is provided.
- **Process isolation** is real only if you deploy `airlock agent` and `airlock executor` as
  separate processes/containers with separate credentials, as intended. Running them in one
  process still prevents the LLM from writing (tool isolation) but weakens defense in depth.
- The audit trail records who approved what, but is not yet append-only or externally
  anchored.

## Reporting a vulnerability

Please do not open a public issue for security reports. Email the maintainers (see the repo
owner's profile) with a description, affected version/commit, and a reproduction. We aim to
acknowledge within a few days. Coordinated disclosure is appreciated.

If you find a way to make the executor perform a write that was not bound to a matching,
unexpired, unconsumed, operator-signed approval, that is the highest-severity class of bug.
