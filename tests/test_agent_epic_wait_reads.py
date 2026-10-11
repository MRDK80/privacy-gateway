"""New bounded-read hooks preserve single-attempt write transports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as handover
from tools.agent_epic_transport import GhTaskTransport


def test_discovery_repository_and_compare_use_explicit_read() -> None:
    calls: list[Sequence[str]] = []

    def read(argv: Sequence[str]) -> str:
        calls.append(argv)
        return (
            '{"full_name":"owner/repo","default_branch":"main"}'
            if len(calls) == 1
            else '{"status":"ahead"}'
        )

    client = branch.GitHubCLI(read=read)
    assert client.repository("owner/repo") == {
        "nameWithOwner": "owner/repo",
        "defaultBranchRef": {"name": "main"},
    }
    assert client.compare("owner/repo", "main", "roadmap/248-demo") == "ahead"
    assert calls[0] == ("gh", "api", "repos/owner/repo")
    assert calls[1][0:2] == ("gh", "api")


def test_task_provenance_uses_explicit_read() -> None:
    calls: list[Sequence[str]] = []

    def read(argv: Sequence[str]) -> str:
        calls.append(argv)
        return (
            '{"number":251,"state":"OPEN","parent":{"number":248},"body":"synthetic"}'
        )

    assert handover.GitHubCLI(read=read).issue("owner/repo", 251)["number"] == 251
    assert len(calls) == 1


def test_transport_json_read_hook_never_receives_write(tmp_path: Path) -> None:
    calls: list[Sequence[str]] = []

    def read(argv: Sequence[str]) -> str:
        calls.append(argv)
        return "{}"

    transport = GhTaskTransport(tmp_path, read=read)
    assert transport.json(("gh", "api", "repos/owner/repo/pulls/1")) == {}
    assert len(calls) == 1
