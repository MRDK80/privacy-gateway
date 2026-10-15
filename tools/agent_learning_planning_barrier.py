"""Read-only original planning prefix barrier before workflow existence."""

from __future__ import annotations

from tools import agent_epic_planning as admission
from tools import agent_learning as core
from tools import agent_learning_records as workflow_records
from tools.agent_epic_loop import RunnerLock
from tools.agent_learning_planning_sources import replay
from tools.agent_learning_store import Store
from tools.agent_learning_verify import Verifier


def planning_barrier(verifier: Verifier, current_task: int) -> None:
    reader = verifier.reader
    reader.guard()
    RunnerLock.current_owned(reader.seed)
    workflow_items = verifier.retained()
    owner = admission.profile(
        reader._json(reader.seed / "planning-profile.json"),
        approved=reader.config["approved_planning_digest"],
        mandate=reader.mandate,
    )
    for task in reader.config["approved_order"]:
        source = admission.Admission(reader.seed, reader.root)._storage(task)
        path = source / "admission.json"
        if not path.exists() and not path.is_symlink():
            continue
        saved = reader._json(path)
        admission.exact(saved, admission.RECORD_KEYS)
        expected = {
            "schema_version": "1.0",
            "repository": reader.owner["repository"],
            "epic": reader.owner["epic"],
            "task": task,
            "planning_digest": reader.config["approved_planning_digest"],
            "mandate_digest": reader.owner["mandate_digest"],
            "policy_sha": reader.owner["policy_sha"],
        }
        if any(saved[key] != value for key, value in expected.items()):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        admission.exact(saved["attempts"], {"planner", "review"})
        hinted = False
        failed = False
        for role, logical, limit_key in [
            ("planner", "planner", "max_planner_attempts"),
            ("review", "planning_review", "max_review_attempts"),
        ]:
            count = core.integer(saved["attempts"][role], owner["limits"][limit_key])
            for ordinal in range(1, count + 1):
                # Missing completed validation/source is UNKNOWN, never empty hints.
                original = replay(reader, task, logical, ordinal)
                envelope = original["request"]["untrusted_verified_hints"]
                hinted = hinted or bool(envelope["hints"])
                failed = failed or original["validation"]["outcome"] == "FAIL"
        if not hinted:
            continue
        # Hinted planning failure cannot disappear merely because no workflow exists.
        # Preserve exact Admission pending state and ask for original disposition.
        if failed or saved["pending"] is not None:
            raise core.LearningError("LEARNING_SOURCE_PENDING")
        try:
            directory = reader.task_directory(task)
        except core.LearningError as error:
            if error.machine_code != "LEARNING_SOURCE_MISSING":
                raise
            if task != current_task:
                raise core.LearningError("LEARNING_SOURCE_PENDING") from None
            continue
        if not (directory / "bounded-workflow").exists():
            if task != current_task:
                raise core.LearningError("LEARNING_SOURCE_PENDING")
            continue
        # Do not skip the existing source/evaluation guard just because Store vanished.
        task_items = []
        learning = directory / "learning"
        if learning.exists() or learning.is_symlink():
            with reader.ownership(directory):
                with Store(
                    learning,
                    reader.root,
                    directory,
                    reader.owner,
                    validators=workflow_records.VALIDATORS,
                ) as retained:
                    retained.ensure_usable()
                    task_items = retained.records()
        verifier._source_complete(task, task_items, workflow_items)
