"""Trusted local snapshot provenance tests for the orchestrator (#196).

Тесты фиксируют контракт задачи #196 до реализации и намеренно красные.

Ожидаемый API в ``tools/agent_orchestrate.py``:

* ``create_trusted_snapshot(contract, *, root)`` возвращает evidence с полями
  ``snapshot_method``, ``snapshot_commit``, ``tree_hash``, ``diff_sha256`` и
  ``provenance_complete``;
* ``assert_tree_unchanged(evidence, *, root)`` возвращает ``None`` либо
  поднимает ``OrchestrationError("TREE_MUTATED_AFTER_SNAPSHOT")``.

Skip и xfail не используются: отсутствие реализации обязано быть видимым.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "agent_orchestrate.py"
BASE_REF = "roadmap/179-codex-adapters"
HEAD_REF = "feat/196-pilot-diff-provenance"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NOT_IMPLEMENTED = "#196: {name} ещё не реализован в tools/agent_orchestrate.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_orchestrate_snapshot_provenance", MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


orchestrator = _load_module()


def _git(root: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return run.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Synthetic User")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    (root / "AGENTS.md").write_text("trusted policy\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("trusted process\n", encoding="utf-8")
    (root / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "unit.py").write_text("UNIT = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", BASE_REF)
    _git(root, "switch", "-c", HEAD_REF)
    (root / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "pkg" / "new_unit.py").write_text("NEW = 1\n", encoding="utf-8")
    return root


def _contract(root: Path, allowed: tuple[str, ...] = ("code.py", "pkg")) -> Any:
    return orchestrator.TaskContract(
        issue=196,
        epic=179,
        acceptance_criteria=("trusted local snapshot",),
        base_ref=BASE_REF,
        base_sha=_git(root, "rev-parse", BASE_REF),
        head_ref=HEAD_REF,
        allowed_paths=allowed,
        max_minutes=10,
        max_repair_iterations=2,
        max_report_chars=200_000,
    )


def _attribute(name: str) -> Callable[..., Any]:
    value = getattr(orchestrator, name, None)
    if value is None:
        pytest.fail(NOT_IMPLEMENTED.format(name=name))
    return cast(Callable[..., Any], value)


def _snapshot(root: Path, allowed: tuple[str, ...] = ("code.py", "pkg")) -> Any:
    factory = _attribute("create_trusted_snapshot")
    return factory(_contract(root, allowed), root=root)


def _field(evidence: Any, name: str) -> Any:
    if isinstance(evidence, dict):
        return evidence.get(name)
    return getattr(evidence, name, None)


def test_head_sha_equals_base_sha_for_uncommitted_change(repository: Path) -> None:
    """Характеризация дефекта #182: HEAD не описывает изменённое дерево."""
    contract = _contract(repository)

    assert orchestrator._head_sha(repository, contract) == contract.base_sha


def test_changed_files_include_untracked_pilot_output(repository: Path) -> None:
    """Scope видит untracked-файлы, поэтому snapshot обязан их включать."""
    contract = _contract(repository)

    assert "pkg/new_unit.py" in orchestrator._changed_files(repository, contract)


def test_tracked_modification_has_stable_identity(repository: Path) -> None:
    evidence = _snapshot(repository)

    assert HEX40.match(str(_field(evidence, "snapshot_commit")))
    assert HEX40.match(str(_field(evidence, "tree_hash")))
    assert HEX64.match(str(_field(evidence, "diff_sha256")))
    assert _field(evidence, "provenance_complete") is True
    assert str(_field(evidence, "snapshot_method"))


def test_repeated_snapshot_of_same_state_is_reproducible(repository: Path) -> None:
    first = _snapshot(repository)
    second = _snapshot(repository)

    assert _field(first, "tree_hash") == _field(second, "tree_hash")
    assert _field(first, "diff_sha256") == _field(second, "diff_sha256")


def test_allowed_untracked_file_changes_identity(repository: Path) -> None:
    before = _snapshot(repository)
    (repository / "pkg" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    after = _snapshot(repository)

    assert _field(before, "tree_hash") != _field(after, "tree_hash")
    assert _field(before, "diff_sha256") != _field(after, "diff_sha256")


def test_deletion_and_rename_are_represented(repository: Path) -> None:
    before = _snapshot(repository)
    (repository / "pkg" / "unit.py").unlink()
    (repository / "code.py").rename(repository / "pkg" / "renamed.py")
    after = _snapshot(repository)

    assert _field(before, "tree_hash") != _field(after, "tree_hash")
    assert _field(after, "provenance_complete") is True


def test_binary_content_is_hashed_without_text_decoding(repository: Path) -> None:
    (repository / "pkg" / "blob.bin").write_bytes(b"\x00\x01\x02\xff")

    evidence = _snapshot(repository)

    assert HEX64.match(str(_field(evidence, "diff_sha256")))


def test_out_of_scope_change_blocks_snapshot(repository: Path) -> None:
    (repository / "outside.py").write_text("OUTSIDE = 1\n", encoding="utf-8")

    with pytest.raises(orchestrator.OrchestrationError) as error:
        _snapshot(repository)

    assert error.value.machine_code == "SNAPSHOT_SCOPE_VIOLATION"


def test_protected_path_change_blocks_snapshot(repository: Path) -> None:
    (repository / "AGENTS.md").write_text("tampered policy\n", encoding="utf-8")

    with pytest.raises(orchestrator.OrchestrationError) as error:
        _snapshot(repository)

    assert error.value.machine_code == "SNAPSHOT_SCOPE_VIOLATION"


def test_mutation_after_snapshot_is_detected(repository: Path) -> None:
    evidence = _snapshot(repository)
    verify = _attribute("assert_tree_unchanged")
    (repository / "code.py").write_text("VALUE = 3\n", encoding="utf-8")

    with pytest.raises(orchestrator.OrchestrationError) as error:
        verify(evidence, root=repository)

    assert error.value.machine_code == "TREE_MUTATED_AFTER_SNAPSHOT"


def test_unchanged_tree_passes_verification(repository: Path) -> None:
    evidence = _snapshot(repository)
    verify = _attribute("assert_tree_unchanged")

    assert verify(evidence, root=repository) is None


def test_snapshot_does_not_move_refs_or_dirty_the_checkout(
    repository: Path,
) -> None:
    before_head = _git(repository, "rev-parse", "HEAD")
    before_refs = _git(repository, "show-ref")
    before_status = _git(repository, "status", "--porcelain=v1")

    _snapshot(repository)

    assert _git(repository, "rev-parse", "HEAD") == before_head
    assert _git(repository, "show-ref") == before_refs
    assert _git(repository, "status", "--porcelain=v1") == before_status


def test_snapshot_works_without_global_git_identity(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI-раннеры не имеют глобальной идентичности; snapshot обязан её задать."""
    empty_config = tmp_path / "absent-gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    _git(repository, "config", "--unset", "user.name")
    _git(repository, "config", "--unset", "user.email")

    evidence = _snapshot(repository)

    assert HEX40.match(str(_field(evidence, "snapshot_commit")))


def test_missing_git_binary_fails_closed(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH очищается после подготовки контракта: отказ обязан быть в коде."""
    contract = _contract(repository)
    factory = _attribute("create_trusted_snapshot")
    empty_dir = tmp_path / "empty-path"
    empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_dir))

    with pytest.raises(orchestrator.OrchestrationError) as error:
        factory(contract, root=repository)

    assert error.value.machine_code == "SNAPSHOT_FAILED"


def test_cleanup_leaves_no_snapshot_artifacts_in_checkout(
    repository: Path,
) -> None:
    before = sorted(path.name for path in repository.iterdir())

    _snapshot(repository)

    assert sorted(path.name for path in repository.iterdir()) == before


def test_evidence_contains_no_private_or_local_paths(repository: Path) -> None:
    evidence = _snapshot(repository)
    payload = evidence if isinstance(evidence, dict) else vars(evidence)
    rendered = repr(payload)

    assert "log_directory" not in rendered
    assert str(repository) not in rendered
    for pattern in orchestrator.PRIVATE_PATTERNS:
        assert pattern not in rendered
