from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tomllib

import pytest

from tools.verify_package_build import (
    BuildGateError,
    WheelFacts,
    ensure_no_license_warnings,
    validate_license_contract,
)

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
DIST_INFO = "privacy_gateway-0.5.0.dist-info"
LICENSE_PATH = f"{DIST_INFO}/licenses/LICENSE"


def test_pyproject_uses_pep639_license_metadata() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert "setuptools>=77.0.3" in config["build-system"]["requires"]
    project = config["project"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert all(
        not classifier.startswith("License ::")
        for classifier in project["classifiers"]
    )


def valid_wheel_facts() -> WheelFacts:
    return WheelFacts(
        generator="setuptools (77.0.3)",
        version="0.5.0",
        license_expression="MIT",
        license_files=("LICENSE",),
        legacy_licenses=(),
        license_classifiers=(),
        license_paths=(LICENSE_PATH,),
        expected_license_path=LICENSE_PATH,
    )


def test_build_gate_accepts_expected_license_contract() -> None:
    validate_license_contract(valid_wheel_facts())
    ensure_no_license_warnings("Successfully built sdist and wheel")


def test_build_gate_rejects_each_license_metadata_violation() -> None:
    valid = valid_wheel_facts()
    invalid = (
        replace(valid, license_expression="Apache-2.0"),
        replace(valid, license_files=()),
        replace(valid, license_files=("COPYING",)),
        replace(valid, legacy_licenses=("MIT",)),
        replace(
            valid,
            license_classifiers=("License :: OSI Approved :: MIT License",),
        ),
        replace(valid, license_paths=(f"{DIST_INFO}/LICENSE",)),
        replace(valid, license_paths=()),
    )

    for facts in invalid:
        with pytest.raises(BuildGateError):
            validate_license_contract(facts)


@pytest.mark.parametrize(
    "warning",
    [
        "SetuptoolsDeprecationWarning: `project.license` as a TOML table is deprecated",
        "SetuptoolsDeprecationWarning: License classifiers are deprecated",
    ],
)
def test_build_gate_rejects_license_deprecation_warnings(warning: str) -> None:
    with pytest.raises(BuildGateError, match="license warnings"):
        ensure_no_license_warnings(warning)
