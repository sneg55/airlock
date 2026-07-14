"""Standalone executor daemon: the only write-capable Airlock process.

It polls the approval store for operator-approved (issued) approvals and runs each through
execute_live, which re-verifies the Ed25519 envelope, re-reads live cloud state, rebuilds
write arguments from trusted fields, and atomically consumes the approval so it can never be
replayed. This process holds the write MCP credential and bearer; the planner/agent process
never does. That capability split is the structural guarantee Airlock exists to enforce.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.agent.monitor import AlibabaReadMonitor
from src.checkpoint.signing import load_public_key
from src.checkpoint.sqlite_store import SQLiteApprovalStore
from src.checkpoint.store import ApprovalStore
from src.common.env import Env, env
from src.executor.alibaba_write import AlibabaOpsWriteClient
from src.executor.execute import ExecutionResult, StateReader, WriteClient, execute_live

logger = logging.getLogger("airlock.executor")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ExecutorService:
    store: ApprovalStore
    write_client: WriteClient
    state_reader: StateReader
    public_key: Ed25519PublicKey
    key_id: str
    bound_account_id: str
    clock: Callable[[], datetime] = field(default=_utc_now)

    @classmethod
    def from_env(cls, settings: Env = env) -> ExecutorService:
        monitor = AlibabaReadMonitor.from_env(settings)
        return cls(
            store=SQLiteApprovalStore(settings.approval_store_path),
            write_client=AlibabaOpsWriteClient.from_env(settings),
            state_reader=monitor.read_state,
            public_key=load_public_key(settings.public_key_path),
            key_id=settings.key_id,
            bound_account_id=settings.bound_account_id,
        )

    def run_once(self) -> list[ExecutionResult]:
        """Execute every issued approval once. Returns one result per attempt."""

        results: list[ExecutionResult] = []
        for record in self.store.list_issued():
            if record.action is None or record.approval is None:
                continue
            result = execute_live(
                record.action,
                record.approval,
                public_key=self.public_key,
                current_key_id=self.key_id,
                bound_account_id=self.bound_account_id,
                store=self.store,
                write_client=self.write_client,
                now=self.clock(),
                state_reader=self.state_reader,
            )
            _log_result(record.approval_id, result)
            results.append(result)
        return results

    def run_forever(self, interval_s: float, *, stop: Event | None = None) -> None:
        logger.info("executor daemon started; polling every %ss", interval_s)
        while stop is None or not stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("executor pass failed; continuing")
            if stop is not None:
                if stop.wait(interval_s):
                    break
            else:
                time.sleep(interval_s)
        logger.info("executor daemon stopped")


def _log_result(approval_id: str, result: ExecutionResult) -> None:
    if result.executed and result.receipt is not None:
        receipt = result.receipt
        logger.info(
            "executed %s: %s %s -> %s",
            approval_id,
            receipt.action,
            receipt.resource_id,
            receipt.status,
        )
    else:
        logger.info("skipped %s: %s", approval_id, result.error_id)
