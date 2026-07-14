from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import ClaimStatus, InMemoryApprovalStore


def test_claim_once_is_atomic(
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
) -> None:
    _token, store, now = issued_token
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store.claim, "approval-1", now) for _index in range(8)]
    results = [future.result() for future in futures]
    assert results.count(ClaimStatus.CLAIMED) == 1
    assert results.count(ClaimStatus.CONSUMED) == 7
