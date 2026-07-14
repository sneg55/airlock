from __future__ import annotations

import json

import httpx
import pytest
from src.checkpoint.cli import main


@pytest.mark.parametrize(
    ("arguments", "method", "path"),
    [
        (["list"], "GET", "/proposals/pending"),
        (["show", "approval-1"], "GET", "/proposals/approval-1"),
        (["approve", "approval-1"], "POST", "/proposals/approval-1/approve"),
        (["reject", "approval-1"], "POST", "/proposals/approval-1/reject"),
    ],
)
def test_cli_commands_call_checkpoint_service(
    arguments: list[str], method: str, path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == method
        assert request.url.path == path
        if method == "POST":
            assert request.headers["Authorization"] == "Bearer cli-token"
        return httpx.Response(200, json={"ok": True})

    result = main(
        ["--url", "http://checkpoint.test", "--token", "cli-token", *arguments],
        transport=httpx.MockTransport(handler),
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
