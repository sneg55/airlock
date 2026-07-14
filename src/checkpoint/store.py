"""Approval lifecycle persistence contracts and backends."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol

from src.checkpoint.signing import SignedApproval
from src.common.canonicalize import action_hash
from src.common.schemas import ProposedAction


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    ISSUED = "issued"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class ClaimStatus(StrEnum):
    CLAIMED = "claimed"
    MISSING = "missing"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class ApprovalStoreError(ValueError):
    """An approval record violates store invariants."""


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    action: ProposedAction | None
    action_hash: str
    approval: SignedApproval | None = None
    approver: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    rejection_reason: str | None = None

    @classmethod
    def pending(cls, approval_id: str, action: ProposedAction) -> ApprovalRecord:
        return cls(
            approval_id=approval_id,
            action=action,
            action_hash=action_hash(action).hex(),
        )

    @classmethod
    def from_signed_approval(cls, approval: SignedApproval, *, approver: str) -> ApprovalRecord:
        return cls(
            approval_id=approval.envelope.approval_id,
            action=None,
            action_hash=approval.envelope.action_hash,
            approval=approval,
            approver=approver,
            status=ApprovalStatus.ISSUED,
        )


class ApprovalStore(Protocol):
    def issue(self, record: ApprovalRecord) -> None: ...

    def create_pending(self, record: ApprovalRecord) -> None: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list_pending(self) -> list[ApprovalRecord]: ...

    def list_issued(self) -> list[ApprovalRecord]: ...

    def issue_pending(
        self, approval_id: str, approval: SignedApproval, *, approver: str
    ) -> ApprovalRecord: ...

    def reject(self, approval_id: str, *, approver: str) -> ApprovalRecord: ...

    def claim(self, approval_id: str, now: datetime) -> ClaimStatus: ...


class InMemoryApprovalStore:
    """Process-local store with every transition protected by one lock."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = Lock()

    def issue(self, record: ApprovalRecord) -> None:
        if record.status is not ApprovalStatus.ISSUED or record.approval is None:
            raise ApprovalStoreError("issue requires an issued approval")
        self._insert(record)

    def create_pending(self, record: ApprovalRecord) -> None:
        if record.status is not ApprovalStatus.PENDING or record.approval is not None:
            raise ApprovalStoreError("pending record cannot contain an approval")
        self._insert(record)

    def _insert(self, record: ApprovalRecord) -> None:
        with self._lock:
            if record.approval_id in self._records:
                raise ApprovalStoreError(f"approval already exists: {record.approval_id}")
            self._records[record.approval_id] = record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self._records.get(approval_id)

    def list_pending(self) -> list[ApprovalRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.status is ApprovalStatus.PENDING]

    def list_issued(self) -> list[ApprovalRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.status is ApprovalStatus.ISSUED]

    def issue_pending(
        self, approval_id: str, approval: SignedApproval, *, approver: str
    ) -> ApprovalRecord:
        with self._lock:
            record = self._pending(approval_id)
            if approval.envelope.approval_id != approval_id:
                raise ApprovalStoreError("signed approval id does not match record")
            if approval.envelope.action_hash != record.action_hash:
                raise ApprovalStoreError("signed approval hash does not match record")
            issued = replace(
                record,
                approval=approval,
                approver=approver,
                status=ApprovalStatus.ISSUED,
            )
            self._records[approval_id] = issued
            return issued

    def reject(self, approval_id: str, *, approver: str) -> ApprovalRecord:
        with self._lock:
            record = self._pending(approval_id)
            rejected = replace(record, approver=approver, status=ApprovalStatus.REJECTED)
            self._records[approval_id] = rejected
            return rejected

    def _pending(self, approval_id: str) -> ApprovalRecord:
        record = self._records.get(approval_id)
        if record is None:
            raise ApprovalStoreError(f"approval does not exist: {approval_id}")
        if record.status is not ApprovalStatus.PENDING:
            raise ApprovalStoreError(f"approval is not pending: {approval_id}")
        return record

    def claim(self, approval_id: str, now: datetime) -> ClaimStatus:
        with self._lock:
            record = self._records.get(approval_id)
            if record is None or record.status in {ApprovalStatus.PENDING, ApprovalStatus.REJECTED}:
                return ClaimStatus.MISSING
            if record.status is ApprovalStatus.CONSUMED:
                return ClaimStatus.CONSUMED
            if record.approval is None:
                raise ApprovalStoreError("issued record has no signed approval")
            if now >= record.approval.envelope.expiry:
                return ClaimStatus.EXPIRED
            self._records[approval_id] = replace(record, status=ApprovalStatus.CONSUMED)
            return ClaimStatus.CLAIMED
