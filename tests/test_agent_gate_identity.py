"""Identity changes invalidate reuse, including dirty/untracked source bytes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tools.agent_gate_identity import fingerprint
from tools.agent_gate_journal import GateBlocked

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only evidence V1")


def test_dirty_and_untracked_inputs_change_identity(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("value = 1\n")
    first = fingerprint(tmp_path, [], {}, {"head": "synthetic"})
    (tmp_path / "source.py").write_text("value = 2\n")
    second = fingerprint(tmp_path, [], {}, {"head": "synthetic"})
    assert first != second
    (tmp_path / "new.py").write_text("value = 3\n")
    assert fingerprint(tmp_path, [], {}, {"head": "synthetic"}) != second


def test_environment_and_external_input_are_bound(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tool = tmp_path / "synthetic-tool"
    tool.write_bytes(b"one")
    first = fingerprint(root, [tool], {"LANG": "C"}, {})
    assert first != fingerprint(root, [tool], {"LANG": "C.UTF-8"}, {})
    tool.write_bytes(b"two")
    assert first != fingerprint(root, [tool], {"LANG": "C"}, {})


def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "linked").symlink_to("missing")
    with pytest.raises(GateBlocked, match="IDENTITY_SYMLINK"):
        fingerprint(tmp_path, [], {}, {})


def test_empty_directory_and_directory_mode_are_source_inputs(tmp_path: Path) -> None:
    first = fingerprint(tmp_path, [], {}, {})
    directory = tmp_path / "synthetic-flag"
    directory.mkdir()
    directory.chmod(0o755)
    second = fingerprint(tmp_path, [], {}, {})
    assert second != first
    directory.chmod(0o700)
    assert fingerprint(tmp_path, [], {}, {}) != second
    directory.rmdir()
    assert fingerprint(tmp_path, [], {}, {}) == first
