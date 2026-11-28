"""Exercise the approved LOC contract without a model or the system keyring."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.models import (
    ConfigurationError,
    DetectedEntity,
    DetectionConfidence,
    EntityType,
    ProcessingStatus,
)
from privacy_gateway.pipeline import prepare_pipeline
from privacy_gateway.restore import restore_text
from privacy_gateway.routing import RoutingConfig


def inject_loc(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def detect(text: str, _config: object) -> list[DetectedEntity]:
        return [
            DetectedEntity(
                EntityType("LOC"),
                match.start(),
                match.end(),
                DetectionConfidence.HIGH,
                "test",
                hashlib.sha256(name.encode()).hexdigest()[:12],
            )
            for match in re.finditer(re.escape(name), text)
        ]

    monkeypatch.setattr("privacy_gateway.pipeline.detect_entities", detect)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Вересковый Плёс", "Вересковый Плёс виден. Вересковый Плёс вдали."),
        ("Asterwick", "We saw Asterwick. Asterwick was quiet."),
    ],
)
def test_loc_prepares_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str
) -> None:
    inject_loc(monkeypatch, name)
    key = generate_key()
    monkeypatch.setattr("privacy_gateway.restore.get_all_keys", lambda: [key])
    result = prepare_pipeline(text, "synthetic", RoutingConfig(), key, tmp_path)
    assert result.status == ProcessingStatus.OK
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert prompt == text.replace(name, "[LOC_1]")
    route = tmp_path / "route.json"
    data = json.loads(route.read_text(encoding="utf-8"))
    assert data["format_version"] == "1.2"
    assert data["token_counts_by_type"] == {"LOC": 2}
    manifest = tmp_path / "manifest.json"
    assert data["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert name not in manifest.read_text(encoding="utf-8")
    assert restore_text(prompt, route).restored_text == text

    for version in ("1.0", "1.1"):
        data["format_version"] = version
        route.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="LOC"):
            restore_text(prompt, route)

    data["format_version"] = "1.2"
    route.write_text(json.dumps(data), encoding="utf-8")
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ConfigurationError, match="integrity check failed"):
        restore_text(prompt, route)


@pytest.mark.parametrize("block", [False, True])
def test_loc_policy_failure_publishes_no_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block: bool
) -> None:
    name = "Asterwick"
    inject_loc(monkeypatch, name)
    policy = RoutingConfig(
        tokenize_types=["EMAIL"], block_unconditionally=["LOC"] if block else []
    )
    result = prepare_pipeline(name, "synthetic", policy, generate_key(), tmp_path)
    assert result.status == ProcessingStatus.BLOCKED
    assert name not in result.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("token", "[ORG_1]", "Invalid LOC token"),
        ("entity_type", "ORG", "Invalid LOC token"),
        ("entity_type", "UNKNOWN_TYPE", "Invalid manifest record"),
    ],
)
def test_loc_restore_rejects_invalid_manifest_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    error: str,
) -> None:
    name = "Asterwick"
    inject_loc(monkeypatch, name)
    key = generate_key()
    monkeypatch.setattr("privacy_gateway.restore.get_all_keys", lambda: [key])
    assert prepare_pipeline(
        name, "synthetic", RoutingConfig(), key, tmp_path
    ).status == ProcessingStatus.OK

    route = tmp_path / "route.json"
    manifest = tmp_path / "manifest.json"
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    entries[0][field] = value
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    data = json.loads(route.read_text(encoding="utf-8"))
    data["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    route.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=error) as exc:
        restore_text("[LOC_1]", route)
    assert name not in str(exc.value)


def test_loc_partial_overlap_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    findings = [
        DetectedEntity(EntityType.LOC, 0, 9, DetectionConfidence.HIGH, "test", "loc"),
        DetectedEntity(EntityType.ORG, 4, 15, DetectionConfidence.HIGH, "test", "org"),
    ]
    monkeypatch.setattr("privacy_gateway.pipeline.detect_entities", lambda *_: findings)
    with pytest.raises(ConfigurationError, match="part of a LOC"):
        prepare_pipeline(
            "Asterwick Works", "synthetic", RoutingConfig(), generate_key(), tmp_path
        )
    assert list(tmp_path.iterdir()) == []


def test_same_value_of_different_types_gets_distinct_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "Asterwick at Asterwick"
    fingerprint = hashlib.sha256(b"Asterwick").hexdigest()[:12]
    findings = [
        DetectedEntity(
            kind, match.start(), match.end(), DetectionConfidence.HIGH,
            "test", fingerprint,
        )
        for kind, match in zip(
            (EntityType.ORG, EntityType.LOC),
            re.finditer("Asterwick", text),
            strict=True,
        )
    ]
    monkeypatch.setattr("privacy_gateway.pipeline.detect_entities", lambda *_: findings)
    assert prepare_pipeline(
        text, "synthetic", RoutingConfig(), generate_key(), tmp_path
    ).status == ProcessingStatus.OK
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "[ORG_1] at [LOC_1]"
