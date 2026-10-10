"""CLI prepare: ошибка получения ключа через реальную точку входа."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.cli import main
from privacy_gateway.keystore import KeyNotFoundError


def test_missing_keyring_key_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отсутствующий ключ даёт exit 4 без traceback и артефактов."""
    source = tmp_path / "input.txt"
    source.write_text("hello world", encoding="utf-8")
    out_dir = tmp_path / "out"
    with (
        patch.object(
            sys, "argv", ["pgw", "prepare", str(source), "--out", str(out_dir)]
        ),
        patch(
            "privacy_gateway.cli.get_key",
            side_effect=KeyNotFoundError("No key found. Run 'pgw key create' first."),
        ) as get_key,
        pytest.raises(SystemExit) as exc,
    ):
        main()
    get_key.assert_called_once_with()
    assert exc.value.code == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "pgw key create" in captured.err
    assert "Traceback" not in captured.err
    assert not out_dir.exists()
