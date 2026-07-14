"""Command-line operator surface for the checkpoint service."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

import httpx

from src.common.env import env


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airlock-approve")
    parser.add_argument("--url", default=env.checkpoint_url, help="checkpoint service URL")
    parser.add_argument(
        "--token",
        default=env.operator_token.get_secret_value(),
        help="operator bearer token",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list pending proposals")
    show = commands.add_parser("show", help="show a proposal and its evidence")
    show.add_argument("approval_id")
    approve = commands.add_parser("approve", help="approve a pending proposal")
    approve.add_argument("approval_id")
    reject = commands.add_parser("reject", help="reject a pending proposal")
    reject.add_argument("approval_id")
    return parser


def _request(client: httpx.Client, command: str, approval_id: str | None, token: str) -> Any:
    if command == "list":
        response = client.get("/proposals/pending")
    elif command == "show" and approval_id is not None:
        response = client.get(f"/proposals/{approval_id}")
    elif command in {"approve", "reject"} and approval_id is not None:
        response = client.post(
            f"/proposals/{approval_id}/{command}",
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        raise ValueError(f"unsupported command: {command}")
    response.raise_for_status()
    return response.json()


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        with httpx.Client(base_url=args.url, transport=transport, timeout=10.0) as client:
            result = _request(
                client,
                args.command,
                getattr(args, "approval_id", None),
                args.token,
            )
    except (httpx.HTTPError, ValueError) as error:
        print(f"checkpoint request failed: {error}", file=sys.stderr)  # noqa: T201
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
