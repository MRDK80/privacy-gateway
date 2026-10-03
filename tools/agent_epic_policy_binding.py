"""Pure internal pinned-policy/moving-base contract; no authority activation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
LEGACY_FIELDS = {"repository", "epic", "roadmap_ref", "policy_sha", "base_sha"}
FIELDS = LEGACY_FIELDS | {"schema_version"}


class BindingError(Exception):
    """Safe machine-code-only failure."""


def import_verified_merge(
    root: Path,
    repository: str,
    base: str,
    merge: str,
    live_merge: Callable[[], str],
    *,
    before_import: Callable[[], None] | None = None,
) -> None:
    """Explicit scoped object import; caller first checks authority and gates."""
    from tools import agent_coordinator_handover as coordinator

    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None or any(
        re.fullmatch(r"[0-9a-f]{40}", sha) is None for sha in (base, merge)
    ):
        raise BindingError("BINDING_INVALID")
    try:
        if coordinator._repository_identity(root) != repository:
            raise BindingError("BINDING_REPOSITORY_MISMATCH")
        if live_merge() != merge:
            raise BindingError("BINDING_BASE_MISMATCH")
        try:
            coordinator._git(root, "cat-file", "-e", f"{merge}^{{commit}}")
        except coordinator.HandoverError:
            if before_import is not None:
                before_import()
            coordinator._git(
                root,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                f"https://github.com/{repository}.git",
                merge,
            )
        coordinator._git(root, "merge-base", "--is-ancestor", base, merge)
        if live_merge() != merge:
            raise BindingError("BINDING_BASE_MISMATCH")
    except BindingError:
        raise
    except Exception:
        raise BindingError("BINDING_FACTS_UNCONFIRMED") from None


def _shape(value: Mapping[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise BindingError("BINDING_INVALID")
    repository = value.get("repository")
    epic = value.get("epic")
    roadmap = value.get("roadmap_ref")
    if (
        not isinstance(repository, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
        or not isinstance(epic, int)
        or isinstance(epic, bool)
        or epic <= 0
        or not isinstance(roadmap, str)
        or not roadmap.startswith(f"roadmap/{epic}-")
        or re.fullmatch(r"roadmap/[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*", roadmap) is None
        or any(
            not isinstance(value.get(key), str)
            or re.fullmatch(r"[0-9a-f]{40}", value[key]) is None
            for key in ("policy_sha", "base_sha")
        )
    ):
        raise BindingError("BINDING_INVALID")


def validate_binding(
    value: Mapping[str, Any],
    *,
    repository: str,
    epic: int,
    roadmap_ref: str,
    policy_sha: str,
    live_base_sha: str,
    is_ancestor: Callable[[str, str], object],
) -> None:
    """Check closed identity against trusted authority and fresh external facts.

    This does not validate or replace a mandate, gate or owner approval. The
    caller must obtain its expected identity and callback from trusted code.
    """
    _shape(value, FIELDS)
    if value["schema_version"] != SCHEMA_VERSION:
        raise BindingError("BINDING_SCHEMA_UNSUPPORTED")
    if (
        value["repository"] != repository
        or value["epic"] != epic
        or not isinstance(epic, int)
        or isinstance(epic, bool)
        or value["roadmap_ref"] != roadmap_ref
        or value["policy_sha"] != policy_sha
    ):
        raise BindingError("BINDING_SCOPE_MISMATCH")
    if value["base_sha"] != live_base_sha:
        raise BindingError("BINDING_BASE_MISMATCH")
    try:
        confirmed = is_ancestor(value["policy_sha"], value["base_sha"])
    except Exception:
        raise BindingError("ANCESTRY_UNCONFIRMED") from None
    if confirmed is not True:
        raise BindingError("ANCESTRY_UNCONFIRMED")


def migrate_legacy_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a candidate only; never infer approval of a moving baseline."""
    _shape(value, LEGACY_FIELDS)
    if value["policy_sha"] != value["base_sha"]:
        raise BindingError("LEGACY_POLICY_BASE_MISMATCH")
    return {"schema_version": SCHEMA_VERSION, **value}


def validate_live_binding(
    value: Mapping[str, Any],
    *,
    repository_root: Path,
    repository: str,
    epic: int,
    roadmap_ref: str,
    policy_sha: str,
    branches: Callable[[str], Mapping[str, str]],
    merge_sha: str | None = None,
) -> None:
    """Validate concrete local Git identity/ancestry and fresh remote ref facts."""
    from tools import agent_coordinator_handover as coordinator

    _shape(value, FIELDS)
    try:
        root = repository_root.resolve()
        if (
            Path(coordinator._git(root, "rev-parse", "--show-toplevel")).resolve()
            != root
            or coordinator._repository_identity(root) != repository
        ):
            raise BindingError("BINDING_REPOSITORY_MISMATCH")
        live = branches(repository).get(roadmap_ref)
        expected = merge_sha if merge_sha is not None else value["base_sha"]
        if live != expected:
            raise BindingError("BINDING_BASE_MISMATCH")
        if merge_sha is not None:
            if re.fullmatch(r"[0-9a-f]{40}", merge_sha) is None:
                raise BindingError("BINDING_INVALID")
            coordinator._git(
                root, "merge-base", "--is-ancestor", value["base_sha"], merge_sha
            )

        def ancestor(policy: str, base: str) -> bool:
            coordinator._git(root, "merge-base", "--is-ancestor", policy, base)
            return True

        validate_binding(
            value,
            repository=repository,
            epic=epic,
            roadmap_ref=roadmap_ref,
            policy_sha=policy_sha,
            live_base_sha=value["base_sha"],
            is_ancestor=ancestor,
        )
    except BindingError:
        raise
    except Exception:
        raise BindingError("BINDING_FACTS_UNCONFIRMED") from None
