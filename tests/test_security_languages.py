"""Security documentation language/contract regressions, synthetic input only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]


def test_detection_guide_does_not_claim_operational_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli("--describe") == 0
    catalog = json.loads(capsys.readouterr().out)
    detect = next(c for c in catalog["commands"] if c["id"] == "detect")
    assert detect["output_formats"] == ["human"]
    text = (ROOT / "docs/detection.md").read_text(encoding="utf-8")
    assert "с JSON-выводом метаданных" not in text
    assert "operational JSON mode is not supported" in " ".join(text.split())
