"""Closed, owner-bounded plan admission; model responses never grant authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tools import agent_consumer_demo as demo
from tools import agent_epic_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools import agent_orchestrate as workflow
from tools.agent_epic_runtime_cli import (
    DeploymentError,
    canonical_digest,
    owner_uid,
    private_json,
    secure_directory,
)

PROFILE_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "tasks",
    "limits",
}
LIMIT_MAXIMA = {
    "max_planner_attempts": 3,
    "max_review_attempts": 3,
    "max_input_chars": 1_000_000,
    "max_output_chars": 200_000,
    "max_paths": 64,
}
PROPOSAL_KEYS = {"schema_version", "plan", "demo_plan", "criteria_mapping"}
REVIEW_KEYS = {"schema_version", "plan_digest", "verdict", "findings"}
RECORD_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "planning_digest",
    "mandate_digest",
    "policy_sha",
    "issue_body_digest",
    "base_sha",
    "default_sha",
    "attempts",
    "pending",
    "status",
    "proposal",
    "review",
}


def block(code: str) -> None:
    raise loop.PhaseBlocked(code)


def exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        block("PLANNING_CONTRACT_INVALID")
    return dict(value)


def strings(value: Any, maximum: int = 200) -> list[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= maximum
        or any(
            not isinstance(item, str)
            or not item
            or item.strip() != item
            or "\x00" in item
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        block("PLANNING_CONTRACT_INVALID")
    return list(value)


def body_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def profile(value: Any, *, approved: str, mandate: Mapping[str, Any]) -> dict[str, Any]:
    checked = exact(value, PROFILE_KEYS)
    if (
        checked["schema_version"] != "1.0"
        or canonical_digest(checked) != approved
        or any(
            checked[key] != mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
        or checked["mandate_digest"] != handover.mandate_digest(mandate)
    ):
        block("PLANNING_APPROVAL_MISMATCH")
    limits = exact(checked["limits"], set(LIMIT_MAXIMA))
    if any(
        type(limits[key]) is not int or not 1 <= limits[key] <= maximum
        for key, maximum in LIMIT_MAXIMA.items()
    ):
        block("PLANNING_LIMITS_INVALID")
    tasks = checked["tasks"]
    if not isinstance(tasks, dict) or not 1 <= len(tasks) <= 200:
        block("PLANNING_PROFILE_INVALID")
    for number, raw in tasks.items():
        if (
            not isinstance(number, str)
            or re.fullmatch(r"[1-9][0-9]*", number) is None
            or number not in mandate["task_grants"]
        ):
            block("TASK_GRANT_MISSING")
        item = exact(raw, {"criteria_sections", "task_class", "demo_template"})
        sections = strings(item["criteria_sections"], 16)
        if any(len(name) > 120 or "\n" in name for name in sections):
            block("CRITERIA_SECTIONS_INVALID")
        if (
            not isinstance(item["task_class"], str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item["task_class"]) is None
        ):
            block("PLANNING_PROFILE_INVALID")
        try:
            template = demo.validate_plan(item["demo_template"])
        except demo.DemoError:
            block("DEMO_PLAN_INVALID")
        if (
            template["task"] != int(number)
            or template["repository"] != mandate["repository"]
            or template["epic"] != mandate["epic"]
            or template["roadmap_ref"] != mandate["roadmap_ref"]
        ):
            block("DEMO_PLAN_INVALID")
    return checked


def extract_criteria(body: str, sections: list[str]) -> list[str]:
    """Extract every source paragraph/bullet, or reject unsupported Markdown."""
    strings(sections, 16)
    lines = body.splitlines()
    headings: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        match = re.fullmatch(r"## ([^#].*?)", line)
        if match:
            headings.setdefault(match[1], []).append(index)
    if any(len(headings.get(name, [])) != 1 for name in sections):
        block("CRITERIA_AMBIGUOUS")
    result: list[str] = []
    for start in sorted(headings[name][0] for name in sections):
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        groups: list[list[str]] = []
        current: list[str] = []
        bullet = False
        for line in lines[start + 1 : end]:
            if not line.strip():
                if current:
                    groups.append(current)
                    current = []
                bullet = False
                continue
            if re.match(r"\s*(?:#{1,6}\s|```|~~~)", line):
                block("CRITERIA_UNSUPPORTED")
            top = re.match(r"(?:[-+*]|\d+[.)]) ", line) is not None
            nested = re.match(r"\s+(?:[-+*]|\d+[.)]) ", line) is not None
            if nested or line.startswith("\t"):
                block("CRITERIA_UNSUPPORTED")
            if top:
                if current:
                    groups.append(current)
                current = [line]
                bullet = True
            elif line.startswith(" "):
                if not current or not bullet or line.startswith("    "):
                    block("CRITERIA_UNSUPPORTED")
                current.append(line)
            else:
                if bullet:
                    block("CRITERIA_UNSUPPORTED")
                current.append(line)
        if current:
            groups.append(current)
        if not groups:
            block("CRITERIA_EMPTY")
        result.extend("\n".join(group) for group in groups)
    return strings(result)


def protected(path: str) -> bool:
    prefixes = (
        *workflow.PROTECTED_PATTERNS,
        *workflow.PRIVATE_PATTERNS,
        "docs/schemas/",
        "docs/ADR-",
        "tools/agent_",
        ".github/",
    )
    return (
        path
        in {
            *workflow.POLICY_FILES,
            "pyproject.toml",
            "setup.cfg",
            "tox.ini",
            ".pre-commit-config.yaml",
            ".secrets.baseline",
        }
        or Path(path).name == "AGENTS.md"
        or any(path.startswith(prefix) for prefix in prefixes)
    )


def validate_proposal(
    value: Any,
    *,
    owner: Mapping[str, Any],
    mandate: Mapping[str, Any],
    context: handover.MandateContext,
    root: Path,
    task: int,
    body: str,
    base: str,
    default: str,
) -> dict[str, Any]:
    proposal = exact(value, PROPOSAL_KEYS)
    if (
        proposal["schema_version"] != "1.0"
        or len(json.dumps(proposal, ensure_ascii=False))
        > owner["limits"]["max_output_chars"]
    ):
        block("PLANNING_RESPONSE_INVALID")
    task_profile = owner["tasks"].get(str(task))
    if task_profile is None:
        block("PLANNING_TASK_NOT_APPROVED")
    criteria = extract_criteria(body, task_profile["criteria_sections"])
    plan = proposal["plan"]
    try:
        handover._validate_inputs(
            mandate,
            plan,
            root=root,
            approved_mandate_digest=owner["mandate_digest"],
            issue_body=body,
            mandate_context=context,
        )
    except handover.PlanningError as error:
        block(error.machine_code)
    if (
        plan["schema_version"] != "2.0"
        or plan["task"] != task
        or plan["base_sha"] != base
        or plan["default_sha"] != default
        or plan["default_ref"] != "main"
        or plan["acceptance_criteria"] != criteria
        or plan["task_class"] != task_profile["task_class"]
    ):
        block("PLANNING_IDENTITY_MISMATCH")
    paths = strings(plan["allowed_paths"], owner["limits"]["max_paths"])
    if any(protected(path) for path in paths):
        block("POLICY_DECISION_REQUIRED")
    for path in paths:
        # A worktree-safe path must also have a safe parent in the fresh base.
        parent = Path(path).parent.as_posix()
        if parent != ".":
            try:
                kind = workflow._git(root, "cat-file", "-t", f"{base}:{parent}")
            except workflow.OrchestrationError:
                block("UNCONFIRMED_DIRECTORY")
            if kind != "tree":
                block("UNCONFIRMED_DIRECTORY")
        try:
            raw = workflow._git(root, "ls-tree", base, "--", path)
        except workflow.OrchestrationError:
            block("STALE_BASE")
        if raw and not raw.startswith(("100644 blob ", "100755 blob ")):
            block("PATH_ESCAPE")
    budgets = exact(
        plan["budgets"], {"max_minutes", "max_repair_iterations", "max_report_chars"}
    )
    remaining = min(
        mandate["expires_at"] - context.now,
        mandate["limits"]["max_duration_seconds"] - (context.now - context.started_at),
    )
    if (
        type(budgets["max_minutes"]) is not int
        or not 1 <= budgets["max_minutes"] <= min(240, remaining // 60)
        or type(budgets["max_repair_iterations"]) is not int
        or not 0 <= budgets["max_repair_iterations"] <= 2
        or type(budgets["max_report_chars"]) is not int
        or not 1 <= budgets["max_report_chars"] <= owner["limits"]["max_output_chars"]
    ):
        block("PLANNING_LIMITS_INVALID")
    indices = plan["delivery_criterion_indices"]
    if (
        not isinstance(indices, list)
        or any(type(i) is not int for i in indices)
        or len(set(indices)) != len(indices)
        or any(i < 0 or i >= len(criteria) for i in indices)
    ):
        block("PLANNING_CRITERIA_INCOMPLETE")
    mapping = proposal["criteria_mapping"]
    if not isinstance(mapping, list) or len(mapping) != len(criteria):
        block("PLANNING_CRITERIA_INCOMPLETE")
    seen: set[int] = set()
    covered: set[str] = set()
    for raw_item in mapping:
        item = exact(raw_item, {"criterion_index", "allowed_paths", "reason"})
        index = item["criterion_index"]
        if (
            type(index) is not int
            or index in seen
            or index not in range(len(criteria))
            or not isinstance(item["reason"], str)
            or not 1 <= len(item["reason"]) <= 512
        ):
            block("PLANNING_CRITERIA_INCOMPLETE")
        assigned = strings(item["allowed_paths"], len(paths))
        if not set(assigned).issubset(paths):
            block("PLAN_SCOPE_EXPANSION")
        seen.add(index)
        covered.update(assigned)
    if covered != set(paths):
        block("PLANNING_SCOPE_NOT_MINIMAL")
    template = copy.deepcopy(task_profile["demo_template"])
    if (
        template["baseline_sha"] != default
        or template["roadmap_sha"] != base
        or proposal["demo_plan"] != template
    ):
        block("DEMO_PLAN_INVALID")
    return copy.deepcopy(proposal)


def validate_review(
    value: Any, proposal: Mapping[str, Any], count: int
) -> dict[str, Any]:
    checked = exact(value, REVIEW_KEYS)
    if (
        checked["schema_version"] != "1.0"
        or checked["plan_digest"] != canonical_digest(proposal)
        or checked["verdict"] not in {"PASS", "NEEDS_DECISION"}
        or not isinstance(checked["findings"], list)
        or len(checked["findings"]) > 200
    ):
        block("PLANNING_REVIEW_INVALID")
    for raw in checked["findings"]:
        item = exact(raw, {"criterion_index", "code"})
        if (
            type(item["criterion_index"]) is not int
            or not -1 <= item["criterion_index"] < count
            or not isinstance(item["code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item["code"]) is None
        ):
            block("PLANNING_REVIEW_INVALID")
    if checked["verdict"] == "PASS" and checked["findings"]:
        block("PLANNING_REVIEW_INVALID")
    return checked


Role = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class Admission:
    """Persist consumed role reservations before invocation, without refund."""

    def __init__(self, state: Path, root: Path) -> None:
        self.state = state
        self.directory = state / "planning"
        self.root = root

    @staticmethod
    def _private_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            block("UNSAFE_RUNTIME_CONFIG")

    def _storage(self, task: int, *, create: bool = False) -> Path:
        directory = self.directory / str(task)
        try:
            # Check the requested ancestry before resolve can erase symlinks.
            for ancestor in (directory, *directory.parents):
                if ancestor.is_symlink():
                    block("UNSAFE_RUNTIME_CONFIG")
            secure_directory(self.state, self.root)
            for child in (self.directory, directory):
                if child.exists():
                    secure_directory(child, self.root)
            for name in ("admission.json", "epic-loop.lock"):
                path = directory / name
                if path.exists() or path.is_symlink():
                    self._private_file(path.lstat())
            if create:
                for child in (self.directory, directory):
                    child.mkdir(mode=0o700, exist_ok=True)
                    secure_directory(child, self.root)
            return directory
        except (OSError, DeploymentError) as error:
            raise loop.PhaseBlocked("UNSAFE_RUNTIME_CONFIG") from error

    @contextmanager
    def _lock(self, directory: Path) -> Iterator[None]:
        try:
            descriptor = os.open(
                directory / "epic-loop.lock",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as error:
            raise loop.LoopError("LOCK_FAILED") from error
        with os.fdopen(descriptor, "r+b") as stream:
            try:
                self._private_file(os.fstat(stream.fileno()))
                loop.RunnerLock._lock(stream)
            except BlockingIOError as error:
                raise loop.LoopError("RUNNER_LOCKED") from error
            except (OSError, DeploymentError) as error:
                raise loop.LoopError("LOCK_FAILED") from error
            try:
                yield
            finally:
                loop.RunnerLock._unlock(stream)

    def admit(
        self,
        *,
        owner: Mapping[str, Any],
        mandate: Mapping[str, Any],
        context: handover.MandateContext,
        task: int,
        body: str,
        base: str,
        default: str,
        dependencies: list[dict[str, Any]],
        invoke: Role,
        guard: Callable[[], None],
        learning: Any = None,
    ) -> dict[str, Any]:
        if str(task) not in owner["tasks"]:
            block("PLANNING_TASK_NOT_APPROVED")
        if len(body) > owner["limits"]["max_input_chars"]:
            block("PLANNING_INPUT_LIMIT")
        try:
            parsed = queue.dependency_facts({"blockedBy": {"nodes": dependencies}})
            if parsed != dependencies or any(
                item["state"] != "CLOSED" for item in parsed
            ):
                raise ValueError
        except (TypeError, ValueError):
            block("TASK_DEPENDENCIES_INVALID")
        guard()
        criteria = extract_criteria(
            body, owner["tasks"][str(task)]["criteria_sections"]
        )
        directory = self._storage(task, create=True)
        store = workflow.StateStore(directory)
        with self._lock(directory):
            identity = {
                "schema_version": "1.0",
                "repository": owner["repository"],
                "epic": owner["epic"],
                "task": task,
                "planning_digest": canonical_digest(owner),
                "mandate_digest": owner["mandate_digest"],
                "policy_sha": owner["policy_sha"],
            }
            evidence = {
                "issue_body_digest": body_digest(body),
                "base_sha": base,
                "default_sha": default,
            }
            self._storage(task)
            path = directory / "admission.json"
            saved = private_json(path) if path.exists() else None
            if saved is None:
                saved = {
                    **identity,
                    **evidence,
                    "attempts": {"planner": 0, "review": 0},
                    "pending": None,
                    "status": "RESERVED",
                    "proposal": None,
                    "review": None,
                }
            else:
                exact(saved, RECORD_KEYS)
                if any(saved[key] != value for key, value in identity.items()):
                    block("PLANNING_STATE_DISPOSITION_REQUIRED")
                attempts = exact(saved["attempts"], {"planner", "review"})
                if any(type(n) is not int or n < 0 for n in attempts.values()):
                    block("PLANNING_RECORD_INVALID")
                if saved["pending"] is not None:
                    block("PLANNING_PENDING_OUTCOME")
                if saved["status"] not in {"RESERVED", "REJECTED", "ADMITTED"}:
                    block("PLANNING_RECORD_INVALID")
                if saved["status"] == "REJECTED":
                    block("PLANNING_NEEDS_DECISION")
                if saved["status"] == "ADMITTED" and all(
                    saved[key] == value for key, value in evidence.items()
                ):
                    response = self._response(saved["proposal"])
                    cached_proposal = validate_proposal(
                        response,
                        owner=owner,
                        mandate=mandate,
                        context=context,
                        root=self.root,
                        task=task,
                        body=body,
                        base=base,
                        default=default,
                    )
                    review = validate_review(
                        self._response(saved["review"]), cached_proposal, len(criteria)
                    )
                    if review["verdict"] != "PASS":
                        block("PLANNING_NEEDS_DECISION")
                    guard()
                    return cached_proposal
                # Evidence drift invalidates admission, never the consumed counters.
                saved.update(evidence, proposal=None, review=None, status="RESERVED")
            request = {
                "schema_version": "1.0",
                "identity": identity,
                "evidence": evidence,
                "issue_body": body,
                "criteria": criteria,
                "dependencies": copy.deepcopy(dependencies),
                "task_profile": owner["tasks"][str(task)],
                "grant": mandate["task_grants"][str(task)],
                "limits": owner["limits"],
                "remaining_seconds": min(
                    mandate["expires_at"] - context.now,
                    mandate["limits"]["max_duration_seconds"]
                    - (context.now - context.started_at),
                ),
                "proposal": None,
            }
            proposal: dict[str, Any] = {}
            for role in ("planner", "review"):
                limit = owner["limits"][
                    "max_planner_attempts"
                    if role == "planner"
                    else "max_review_attempts"
                ]
                if saved["attempts"][role] >= limit:
                    block("PLANNING_ATTEMPTS_EXHAUSTED")
                if (
                    len(json.dumps(request, ensure_ascii=False))
                    > owner["limits"]["max_input_chars"]
                ):
                    block("PLANNING_INPUT_LIMIT")
                if learning is not None:
                    learning.current_context(context, guard)
                wire_request = (learning.prepare(role, copy.deepcopy(request))
                                if learning is not None else copy.deepcopy(request))
                guard()
                saved["attempts"][role] += 1
                saved["pending"] = {
                    "role": role,
                    "attempt": saved["attempts"][role],
                    "request_digest": canonical_digest(wire_request),
                }
                store.save("admission", saved)
                guard()
                if learning is not None:
                    learning.reserve_planning(role, wire_request, context=context)
                # A transport exception preserves the exact unfinished reservation.
                response = dict(invoke(role, wire_request))
                # Evidence drift cannot complete this reserved request, even if
                # the model returned a valid response. Preserve pending intent.
                guard()
                if (
                    len(json.dumps(response, ensure_ascii=False))
                    > owner["limits"]["max_output_chars"]
                ):
                    block("PLANNING_OUTPUT_LIMIT")
                def validated_response() -> dict[str, Any]:
                    if role == "planner":
                        return validate_proposal(
                            response,
                            owner=owner,
                            mandate=mandate,
                            context=context,
                            root=self.root,
                            task=task,
                            body=body,
                            base=base,
                            default=default,
                        )
                    return validate_review(response, proposal, len(criteria))

                validated = (
                    learning.validate_planning(
                        role, wire_request, response, validate=validated_response,
                    )
                    if learning is not None else validated_response()
                )
                if role == "planner":
                    proposal = validated
                    request["proposal"] = proposal
                else:
                    response = validated
                saved["proposal" if role == "planner" else "review"] = {
                    "digest": canonical_digest(response),
                    "response": response,
                }
                saved["pending"] = None
                if role == "review":
                    saved["status"] = (
                        "ADMITTED" if response["verdict"] == "PASS" else "REJECTED"
                    )
                store.save("admission", saved)
                guard()
                if learning is not None:
                    learning.complete_planning(role, wire_request, response)
            if saved["status"] != "ADMITTED":
                block("PLANNING_NEEDS_DECISION")
            return proposal

    @staticmethod
    def _response(value: Any) -> dict[str, Any]:
        envelope = exact(value, {"digest", "response"})
        if not isinstance(envelope["response"], dict) or envelope[
            "digest"
        ] != canonical_digest(envelope["response"]):
            block("PLANNING_RECORD_INVALID")
        return dict(envelope["response"])

    def recipe(self, task: int, planning_digest: str) -> Mapping[str, Any]:
        directory = self._storage(task)
        record = private_json(directory / "admission.json")
        exact(record, RECORD_KEYS)
        if (
            record["planning_digest"] != planning_digest
            or record["pending"] is not None
            or record["status"] != "ADMITTED"
        ):
            block("PLANNING_ADMISSION_REQUIRED")
        proposal = self._response(record["proposal"])
        review = validate_review(
            self._response(record["review"]),
            proposal,
            len(proposal["plan"]["acceptance_criteria"]),
        )
        if review["verdict"] != "PASS":
            block("PLANNING_ADMISSION_REQUIRED")
        return {"plan": proposal["plan"], "demo_plan": proposal["demo_plan"]}

    def confirm(
        self,
        *,
        owner: Mapping[str, Any],
        mandate: Mapping[str, Any],
        context: handover.MandateContext,
        task: int,
        body: str,
        base: str,
        default: str,
    ) -> Mapping[str, Any]:
        """Read-only fresh receipt check; never invoke a role or refill attempts."""
        self._storage(task, create=False)
        record = private_json(self.directory / str(task) / "admission.json")
        exact(record, RECORD_KEYS)
        expected = {
            "repository": owner["repository"],
            "epic": owner["epic"],
            "task": task,
            "planning_digest": canonical_digest(owner),
            "mandate_digest": owner["mandate_digest"],
            "policy_sha": owner["policy_sha"],
            "issue_body_digest": body_digest(body),
            "base_sha": base,
            "default_sha": default,
        }
        if (
            record["schema_version"] != "1.0"
            or record["status"] != "ADMITTED"
            or record["pending"] is not None
            or any(record[key] != value for key, value in expected.items())
        ):
            block("PLANNING_ADMISSION_CHANGED")
        proposal = validate_proposal(
            self._response(record["proposal"]),
            owner=owner,
            mandate=mandate,
            context=context,
            root=self.root,
            task=task,
            body=body,
            base=base,
            default=default,
        )
        review = validate_review(
            self._response(record["review"]),
            proposal,
            len(proposal["plan"]["acceptance_criteria"]),
        )
        if review["verdict"] != "PASS":
            block("PLANNING_ADMISSION_REQUIRED")
        return {"plan": proposal["plan"], "demo_plan": proposal["demo_plan"]}
