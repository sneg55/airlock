"""FastAPI checkpoint service for human-reviewed approvals."""

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from src.checkpoint.signing import SignedApproval, build_and_sign, load_private_key
from src.checkpoint.sqlite_store import SQLiteApprovalStore
from src.checkpoint.store import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStore,
    ApprovalStoreError,
)
from src.common.env import Env, env
from src.common.schemas import ProposedAction

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class CheckpointConfig:
    operator_token: str
    operator_identity: str
    private_key: Ed25519PrivateKey
    key_id: str
    bound_account_id: str
    approval_ttl: timedelta = timedelta(minutes=5)

    @classmethod
    def from_env(cls, settings: Env = env) -> "CheckpointConfig":
        return cls(
            operator_token=settings.operator_token.get_secret_value(),
            operator_identity=settings.operator_identity,
            private_key=load_private_key(settings.signing_key_path),
            key_id=settings.key_id,
            bound_account_id=settings.bound_account_id,
        )


class ProposalCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    action_hash: str


class ProposalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    action_hash: str
    status: ApprovalStatus
    action: ProposedAction | None
    approver: str | None

    @classmethod
    def from_record(cls, record: ApprovalRecord) -> "ProposalView":
        return cls(
            approval_id=record.approval_id,
            action_hash=record.action_hash,
            status=record.status,
            action=record.action,
            approver=record.approver,
        )


def _operator_dependency(config: CheckpointConfig) -> Callable[[str | None], str]:
    def authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, separator, credential = (authorization or "").partition(" ")
        scheme_ok = hmac.compare_digest(scheme.lower(), "bearer")
        token_ok = hmac.compare_digest(credential, config.operator_token)
        if not separator or not scheme_ok or not token_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="valid operator bearer credential required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return config.operator_identity

    return authenticate


def _record_or_404(store: ApprovalStore, approval_id: str) -> ApprovalRecord:
    record = store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found")
    return record


def _transition_conflict(error: ApprovalStoreError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def create_app(
    config: CheckpointConfig | None = None,
    *,
    store: ApprovalStore | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create a configured checkpoint application.

    Operator routes should also be network-isolated in deployment. Bearer authentication
    remains mandatory here even when that isolation is present.
    """

    active_config = config or CheckpointConfig.from_env()
    active_store = store or _store_from_env(env)
    active_clock = clock or (lambda: datetime.now(UTC))
    require_operator = _operator_dependency(active_config)
    app = FastAPI(title="Airlock checkpoint service")
    app.state.approval_store = active_store

    @app.post("/proposals", response_model=ProposalCreated, status_code=status.HTTP_201_CREATED)
    def _submit(action: ProposedAction) -> ProposalCreated:
        if action.cloud_account_id != active_config.bound_account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="proposal account is not bound to this checkpoint",
            )
        record = ApprovalRecord.pending(str(uuid4()), action)
        active_store.create_pending(record)
        return ProposalCreated(approval_id=record.approval_id, action_hash=record.action_hash)

    @app.get("/proposals/pending", response_model=list[ProposalView])
    def _pending() -> list[ProposalView]:
        return [ProposalView.from_record(record) for record in active_store.list_pending()]

    @app.get("/proposals/{approval_id}", response_model=ProposalView)
    def _show(approval_id: str) -> ProposalView:
        return ProposalView.from_record(_record_or_404(active_store, approval_id))

    @app.post("/proposals/{approval_id}/approve", response_model=SignedApproval)
    def _approve(
        approval_id: str,
        approver: Annotated[str, Depends(require_operator)],
    ) -> SignedApproval:
        record = _record_or_404(active_store, approval_id)
        if record.status is not ApprovalStatus.PENDING or record.action is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="proposal not pending")
        now = active_clock()
        approval = build_and_sign(
            record.action,
            active_config.private_key,
            approval_id=approval_id,
            nonce=secrets.token_hex(16),
            issued_at=now,
            expiry=now + active_config.approval_ttl,
            key_id=active_config.key_id,
        )
        try:
            active_store.issue_pending(approval_id, approval, approver=approver)
        except ApprovalStoreError as error:
            raise _transition_conflict(error) from error
        return approval

    @app.post("/proposals/{approval_id}/reject", response_model=ProposalView)
    def _reject(
        approval_id: str,
        approver: Annotated[str, Depends(require_operator)],
    ) -> ProposalView:
        _record_or_404(active_store, approval_id)
        try:
            record = active_store.reject(approval_id, approver=approver)
        except ApprovalStoreError as error:
            raise _transition_conflict(error) from error
        return ProposalView.from_record(record)

    app.state.route_handlers = (_submit, _pending, _show, _approve, _reject)
    return app


def _store_from_env(settings: Env) -> SQLiteApprovalStore:
    path = Path(settings.approval_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteApprovalStore(path)
