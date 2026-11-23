"""Exercise the opt-in NER boundary without the optional model packages."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from privacy_gateway import local_ner
from privacy_gateway.crypto import generate_key
from privacy_gateway.detector import detect_entities, load_config
from privacy_gateway.models import ConfigurationError, ProcessingStatus
from privacy_gateway.pipeline import prepare_pipeline
from privacy_gateway.routing import RoutingConfig


def write_config(tmp_path: Path, *, enabled: bool = True, content: str = "") -> Path:
    config = tmp_path / "entities.yaml"
    ner = "local_ner: {model_dir: models}\n" if enabled else ""
    config.write_text(ner + content or "dictionary: {}\n", encoding="utf-8")
    return config


def test_default_detector_avoids_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Mock(side_effect=AssertionError("unexpected local NER"))
    monkeypatch.setattr(local_ner, "_engine", engine)
    path = write_config(tmp_path, enabled=False)
    assert detect_entities("Asterwick", load_config(path)) == []
    engine.assert_not_called()


@pytest.mark.parametrize("section", ["null", "{}", "{model_dir: 3}", "{model_dir: ''}"])
def test_invalid_enablement_fails_closed(tmp_path: Path, section: str) -> None:
    config = write_config(tmp_path, enabled=False, content=f"local_ner: {section}\n")
    with pytest.raises(ConfigurationError, match="local_ner"):
        load_config(config)


@pytest.mark.parametrize("failure", ["missing", "incompatible", "inference"])
def test_enabled_failure_publishes_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = write_config(tmp_path)
    if failure == "missing":
        engine = local_ner._engine
        engine.cache_clear()
        monkeypatch.setattr(
            local_ner,
            "version",
            {
                "onnxruntime": "1.23.2",
                "tokenizers": "0.22.2",
                "spacy": "3.8.18",
            }.__getitem__,
        )
    elif failure == "incompatible":
        monkeypatch.setattr(local_ner, "version", lambda _: "invalid-version")
    else:
        broken = Mock()
        broken.spans.side_effect = RuntimeError("synthetic probe value")
        monkeypatch.setattr(local_ner, "_engine", lambda _: broken)
    output = tmp_path / "out"
    with pytest.raises(ConfigurationError, match="Enabled local NER") as exc:
        prepare_pipeline(
            "synthetic probe value",
            "synthetic",
            RoutingConfig(),
            generate_key(),
            output,
            entities_config_path=path,
        )
    assert "synthetic probe value" not in str(exc.value)
    assert not output.exists()


@pytest.mark.parametrize(
    ("model_interval", "expected"),
    [
        ((0, 9, "LOC"), ProcessingStatus.OK),
        ((0, 15, "LOC"), ProcessingStatus.BLOCKED),
        ((10, 15, "LOC"), ProcessingStatus.OK),
    ],
)
def test_overlap_requires_full_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_interval: tuple[int, int, str],
    expected: ProcessingStatus,
) -> None:
    path = write_config(tmp_path, content="dictionary: {ORG: [Asterwick]}\n")
    monkeypatch.setattr(local_ner, "model_spans_for", lambda *_: {model_interval})
    output = tmp_path / "out"
    config = load_config(path)
    assert config.ner_model_dir == tmp_path / "models"
    findings = detect_entities("Asterwick Works", config)
    assert findings[0].entity_type.value == "ORG"
    result = prepare_pipeline(
        "Asterwick Works",
        "synthetic",
        RoutingConfig(),
        generate_key(),
        output,
        entities_config_path=path,
    )
    assert result.status == expected
    if expected == ProcessingStatus.BLOCKED:
        assert not output.exists()
    else:
        assert result.prompt_path is not None
        prompt = result.prompt_path.read_text(encoding="utf-8")
        assert prompt == ("[ORG_1] [LOC_1]" if model_interval[0] else "[ORG_1] Works")


def test_custom_policy_cannot_leak_recognized_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path)
    monkeypatch.setattr(local_ner, "model_spans_for", lambda *_: {(0, 9, "PERSON")})
    output = tmp_path / "out"
    result = prepare_pipeline(
        "Asterwick",
        "synthetic",
        RoutingConfig(tokenize_types=["ORG"]),
        generate_key(),
        output,
        entities_config_path=config,
    )
    assert result.status == ProcessingStatus.BLOCKED
    assert not output.exists()


def test_detect_cli_reports_missing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from privacy_gateway.cli import main

    config = write_config(tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("Asterwick", encoding="utf-8")
    monkeypatch.setattr(local_ner, "_engine", Mock(side_effect=FileNotFoundError))
    monkeypatch.setattr(
        sys, "argv", ["pgw", "detect", str(source), "--config", str(config)]
    )
    with pytest.raises(SystemExit) as status:
        main()
    assert status.value.code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Asterwick" not in captured.err
    assert "Enabled local NER" in captured.err


def test_unconditional_block_covers_dropped_model_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, content="dictionary: {ORG: [Asterwick]}\n")
    monkeypatch.setattr(local_ner, "model_spans_for", lambda *_: {(0, 9, "LOC")})
    policy = RoutingConfig(tokenize_types=["ORG"], block_unconditionally=["LOC"])
    output = tmp_path / "out"
    result = prepare_pipeline(
        "Asterwick",
        "synthetic",
        policy,
        generate_key(),
        output,
        entities_config_path=config,
    )
    assert result.status == ProcessingStatus.BLOCKED
    assert not output.exists()
