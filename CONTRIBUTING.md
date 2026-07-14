# Contributing to Airlock

Thanks for your interest. Airlock is a security tool, so contributions are held to a high
bar for clarity and test coverage, especially anywhere near the approval path.

## Development setup

```bash
uv sync --dev            # Python deps (needs uv + CPython 3.13)
uv run pytest -q         # tests
uv run ruff check .      # lint
uv run pyright           # types
cd web && npm install    # web app deps (needs Node 20+)
```

`uv run airlock --help` shows the component commands.

## Ground rules

- **Never weaken the core invariant.** The planner never holds a write credential, a write
  MCP tool, or the signing key. The executor never holds the private signing key. Write
  arguments are reconstructed from trusted fields, never a free-form blob. See `SECURITY.md`.
- **The canonicalizer is shared and frozen.** Signer and verifier import the one
  canonicalizer in `src/common/`. Any change requires regenerating the conformance vectors
  in `eval/` and must be called out explicitly in the PR.
- **Tests are required** for behavior changes. The approval, verification, and executor paths
  must stay green, and new branches need coverage. Prefer real objects over mocks at the
  security boundary.
- **Keep files focused.** The repo enforces small, single-purpose files; a file growing past
  ~300 lines is a signal to split it.
- **No em dashes in prose**, per the project style.

## Pull requests

1. Branch from `main`; keep the change scoped to one concern.
2. Ensure `ruff`, `pyright`, `pytest`, and the web `lint`/`build` all pass (CI runs these).
3. Describe *why*, not just *what*. If the change touches the security model, explain the
   threat it addresses or the property it preserves.

## Adding a cloud provider

The approval gate is provider-agnostic. A new provider needs: a read monitor (discover +
metrics + state re-read), a write client constrained to an allowlist of actions, and RAM/IAM
policy templates with separated read and write credentials. Keep the checkpoint and executor
verification untouched.
