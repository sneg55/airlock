"""Durable SQLite implementation of the approval store protocol."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Lock

from src.checkpoint.signing import ApprovalEnvelope, SignedApproval
from src.checkpoint.store import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStoreError,
    ClaimStatus,
)
from src.common.schemas import ProposedAction


class SQLiteApprovalStore:
    """Durable SQLite backend with transactional lifecycle transitions."""

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY, status TEXT NOT NULL, action_json TEXT,
                action_hash TEXT NOT NULL, envelope_json TEXT, signature BLOB,
                approver TEXT, rejection_reason TEXT
            )"""
        )
        self._connection.commit()

    def issue(self, record: ApprovalRecord) -> None:
        if record.status is not ApprovalStatus.ISSUED or record.approval is None:
            raise ApprovalStoreError("issue requires an issued approval")
        self._insert(record)

    def create_pending(self, record: ApprovalRecord) -> None:
        if record.status is not ApprovalStatus.PENDING or record.approval is not None:
            raise ApprovalStoreError("pending record cannot contain an approval")
        self._insert(record)

    def _insert(self, record: ApprovalRecord) -> None:
        values = self._serialize(record)
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values
                )
                self._connection.commit()
            except sqlite3.IntegrityError as error:
                raise ApprovalStoreError(
                    f"approval already exists: {record.approval_id}"
                ) from error

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._deserialize(row) if row is not None else None

    def list_pending(self) -> list[ApprovalRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY rowid",
                (ApprovalStatus.PENDING,),
            ).fetchall()
        return [self._deserialize(row) for row in rows]

    def list_issued(self) -> list[ApprovalRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY rowid",
                (ApprovalStatus.ISSUED,),
            ).fetchall()
        return [self._deserialize(row) for row in rows]

    def issue_pending(
        self, approval_id: str, approval: SignedApproval, *, approver: str
    ) -> ApprovalRecord:
        with self._lock:
            record = self._get_pending(approval_id)
            if approval.envelope.approval_id != approval_id:
                raise ApprovalStoreError("signed approval id does not match record")
            if approval.envelope.action_hash != record.action_hash:
                raise ApprovalStoreError("signed approval hash does not match record")
            issued = replace(
                record, approval=approval, approver=approver, status=ApprovalStatus.ISSUED
            )
            self._update(issued, expected=ApprovalStatus.PENDING)
            return issued

    def reject(self, approval_id: str, *, approver: str) -> ApprovalRecord:
        with self._lock:
            record = self._get_pending(approval_id)
            rejected = replace(record, approver=approver, status=ApprovalStatus.REJECTED)
            self._update(rejected, expected=ApprovalStatus.PENDING)
            return rejected

    def _get_pending(self, approval_id: str) -> ApprovalRecord:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalStoreError(f"approval does not exist: {approval_id}")
        record = self._deserialize(row)
        if record.status is not ApprovalStatus.PENDING:
            raise ApprovalStoreError(f"approval is not pending: {approval_id}")
        return record

    def _update(self, record: ApprovalRecord, *, expected: ApprovalStatus) -> None:
        values = self._serialize(record)
        cursor = self._connection.execute(
            """UPDATE approvals SET status=?, action_json=?, action_hash=?, envelope_json=?,
               signature=?, approver=?, rejection_reason=?
               WHERE approval_id=? AND status=?""",
            (*values[1:], values[0], expected),
        )
        if cursor.rowcount != 1:
            self._connection.rollback()
            raise ApprovalStoreError(f"approval transition failed: {record.approval_id}")
        self._connection.commit()

    def claim(self, approval_id: str, now: datetime) -> ClaimStatus:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                return ClaimStatus.MISSING
            record = self._deserialize(row)
            if record.status in {ApprovalStatus.PENDING, ApprovalStatus.REJECTED}:
                return ClaimStatus.MISSING
            if record.status is ApprovalStatus.CONSUMED:
                return ClaimStatus.CONSUMED
            if record.approval is None:
                raise ApprovalStoreError("issued record has no signed approval")
            if now >= record.approval.envelope.expiry:
                return ClaimStatus.EXPIRED
            self._update(
                replace(record, status=ApprovalStatus.CONSUMED),
                expected=ApprovalStatus.ISSUED,
            )
            return ClaimStatus.CLAIMED

    @staticmethod
    def _serialize(record: ApprovalRecord) -> tuple[object, ...]:
        action_json = record.action.model_dump_json() if record.action is not None else None
        envelope_json = None
        signature = None
        if record.approval is not None:
            envelope_json = record.approval.envelope.model_dump_json()
            signature = record.approval.signature
        return (
            record.approval_id,
            record.status,
            action_json,
            record.action_hash,
            envelope_json,
            signature,
            record.approver,
            record.rejection_reason,
        )

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> ApprovalRecord:
        action = (
            ProposedAction.model_validate_json(row["action_json"]) if row["action_json"] else None
        )
        approval = None
        if row["envelope_json"] is not None:
            envelope = ApprovalEnvelope.model_validate_json(row["envelope_json"])
            approval = SignedApproval(envelope=envelope, signature=row["signature"])
        return ApprovalRecord(
            approval_id=row["approval_id"],
            action=action,
            action_hash=row["action_hash"],
            approval=approval,
            approver=row["approver"],
            status=ApprovalStatus(row["status"]),
            rejection_reason=row["rejection_reason"],
        )
