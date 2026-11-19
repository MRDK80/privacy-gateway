"""Release workflow role separation and non-publishing PR safety."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.safe_load((ROOT / ".github/workflows/publish-pypi.yml").read_text()),
    )


def test_only_explicit_dispatch_can_request_publish() -> None:
    data = workflow()
    assert set(data["on"]) == {"workflow_dispatch"}
    inputs = data["on"]["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["default"] == "dry-run"
    assert inputs["mode"]["options"] == ["dry-run", "publish"]
    assert data["permissions"] == {}
    publish = data["jobs"]["publish"]
    assert publish["needs"] == "verify"
    assert "inputs.mode == 'publish'" in publish["if"]
    assert "github.ref == 'refs/heads/main'" in publish["if"]
    assert "needs.verify.outputs.publish_ready == 'true'" in publish["if"]
    assert "||" not in publish["if"]
    assert publish["environment"]["name"] == "pypi"


def test_only_publisher_has_oidc_and_no_package_execution() -> None:
    jobs = workflow()["jobs"]
    assert set(jobs) == {"build", "verify", "publish"}
    for name, job in jobs.items():
        permissions = job["permissions"]
        assert permissions.get("contents") == "read"
        assert permissions.get("id-token") == ("write" if name == "publish" else None)
        assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] <= 40
        if name == "publish":
            for step in job["steps"]:
                assert not step.get("uses", "").startswith("actions/checkout@")
                assert not any(
                    word in step.get("run", "")
                    for word in ("python", "pip install", "source/")
                )
                assert "secrets." not in str(step)
    publisher = [
        step
        for step in jobs["publish"]["steps"]
        if step.get("uses", "").startswith("pypa/")
    ]
    assert len(publisher) == 1
    assert publisher[0]["with"]["skip-existing"] is False


def test_verifier_uses_fresh_exact_policy_and_immutable_run_artifacts() -> None:
    jobs = workflow()["jobs"]
    verify = jobs["verify"]
    assert verify["needs"] == "build"
    checkouts = [
        step
        for step in verify["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert {step["with"]["path"]: step["with"]["ref"] for step in checkouts} == {
        "policy": "${{ github.workflow_sha }}",
        "source": "${{ inputs.source_sha }}",
    }
    assert all(step["with"]["persist-credentials"] is False for step in checkouts)
    assert not any("pip install" in step.get("run", "") for step in verify["steps"])
    for job, needed in (
        (verify, "${{ needs.build.outputs.raw_id }}"),
        (jobs["publish"], "${{ needs.verify.outputs.verified_id }}"),
    ):
        download = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        ]
        assert len(download) == 1
        assert download[0]["with"]["artifact-ids"] == needed
        assert not {"run-id", "github-token", "repository"} & set(download[0]["with"])
    digest = next(
        step
        for step in jobs["publish"]["steps"]
        if step.get("name") == "Verify trusted digest and exact transferred file set"
    )
    assert digest["env"]["EXPECTED_SUMS"] == "${{ needs.verify.outputs.sums_sha256 }}"
    assert "sha256sum --check --strict" in digest["run"]


def test_external_actions_are_pinned_and_inputs_are_not_shell_code() -> None:
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(
                    r"(?:actions|pypa)/[a-z-]+@[0-9a-f]{40}", step["uses"]
                )
            assert "${{ inputs." not in step.get("run", "")


def test_artifact_id_is_required_before_every_download() -> None:
    for name in ("verify", "publish"):
        steps = workflow()["jobs"][name]["steps"]
        guard = [
            i
            for i, step in enumerate(steps)
            if step.get("name") == "Require immutable artifact ID"
        ]
        download = next(
            i
            for i, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        assert len(guard) == 1 and guard[0] < download


@pytest.mark.skipif(
    sys.platform != "linux", reason="release hash/ID job uses Ubuntu bash"
)
@pytest.mark.parametrize("job", ["verify", "publish"])
@pytest.mark.parametrize(
    "value,success",
    [
        ("", False),
        ("0", False),
        ("-1", False),
        ("1,2", False),
        ("1; echo unsafe", False),
        ("42", True),
    ],
)
def test_invalid_artifact_ids_cannot_fall_back_to_all_artifacts(
    job: str,
    value: str,
    success: bool,
) -> None:
    guard = next(
        step
        for step in workflow()["jobs"][job]["steps"]
        if step.get("name") == "Require immutable artifact ID"
    )
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, "-e", "-c", guard["run"]],
        env={"ARTIFACT_ID": value},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) == success
    assert result.stdout == b"" and result.stderr == b""
