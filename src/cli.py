"""The `airlock` command: run each component as its own isolated process.

Subcommands:
  airlock checkpoint   serve the approval checkpoint (the signing key lives here)
  airlock agent        one read-only discovery + proposal pass (--loop to repeat)
  airlock executor     the write-only daemon that executes approved actions
  airlock approvals    operator surface: list / show / approve / reject

The split is deliberate: `agent` holds only read capability, `executor` holds the write
credential, and only `checkpoint` can sign. Run them as separate processes (or containers)
so the guarantee is a deployment boundary, not just a code convention.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from src.common.env import env


def _out(message: str) -> None:
    print(message)  # noqa: T201 - CLI stdout is this command's product


def _run_checkpoint(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.checkpoint.service:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=env.log_level,
    )
    return 0


def _run_agent(args: argparse.Namespace) -> int:
    from src.agent.runner import AgentRunner

    runner = AgentRunner.from_env()
    while True:
        for result in runner.run_once():
            _out(f"[{result.status}] {result.resource_id}: {result.reason}")
        if not args.loop:
            return 0
        time.sleep(env.agent_poll_interval_s)


def _run_executor(args: argparse.Namespace) -> int:
    from src.executor.service import ExecutorService

    service = ExecutorService.from_env()
    if args.once:
        service.run_once()
        return 0
    service.run_forever(env.executor_poll_interval_s)
    return 0


def _run_approvals(argv: list[str]) -> int:
    from src.checkpoint.cli import main as approvals_main

    return approvals_main(argv)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="airlock", description="Human-gated cloud cleanup.")
    sub = parser.add_subparsers(dest="command", required=True)

    checkpoint = sub.add_parser("checkpoint", help="serve the approval checkpoint")
    checkpoint.add_argument("--host", default="127.0.0.1")
    checkpoint.add_argument("--port", type=int, default=8000)

    agent = sub.add_parser("agent", help="run a read-only discovery + proposal pass")
    agent.add_argument("--loop", action="store_true", help="keep running on an interval")

    executor = sub.add_parser("executor", help="run the write-only executor daemon")
    executor.add_argument("--once", action="store_true", help="one pass then exit")

    sub.add_parser("approvals", help="operator surface (list/show/approve/reject)", add_help=False)

    args, rest = parser.parse_known_args(argv)
    if args.command == "checkpoint":
        return _run_checkpoint(args)
    if args.command == "agent":
        return _run_agent(args)
    if args.command == "executor":
        return _run_executor(args)
    if args.command == "approvals":
        return _run_approvals(rest)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
