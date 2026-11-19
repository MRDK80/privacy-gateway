"""Public library documentation and import smoke regressions."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest

import privacy_gateway
import privacy_gateway.facade as facade


def test_facade_does_not_document_an_unimplemented_context_manager() -> None:
    gateway = privacy_gateway.PrivacyGateway
    assert not hasattr(gateway, "__enter__")
    assert gateway.__module__ == "privacy_gateway.facade"
    assert facade.__doc__ is not None
    assert "либо через контекстный менеджер" not in facade.__doc__


def test_public_exports_and_documented_methods_have_english_docstrings() -> None:
    for name in privacy_gateway.__all__:
        exported = getattr(privacy_gateway, name)
        if name == "__version__":
            assert isinstance(exported, str)
            continue
        doc = inspect.getdoc(exported)
        assert doc
        assert not re.search("[А-Яа-яЁё]", doc), name
    for owner, names in (
        (privacy_gateway.PrivacyGateway, ("prepare", "restore", "discard", "config")),
        (privacy_gateway.RestoreContext, ("to_token", "from_token")),
    ):
        for name in names:
            doc = inspect.getdoc(getattr(owner, name))
            assert doc and not re.search("[А-Яа-яЁё]", doc), name


def test_library_reference_snippets_run_in_isolated_synthetic_sandbox(
    sandbox: Path, fernet_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_backend() -> None:
        raise AssertionError("No real keyring backend may be selected")

    monkeypatch.setattr("keyring.get_keyring", forbidden_backend)
    monkeypatch.setattr("privacy_gateway.keystore.get_key", lambda: fernet_key)
    monkeypatch.setattr("privacy_gateway.restore.get_all_keys", lambda: [fernet_key])
    text = "Email user@example.com"
    namespace: dict[str, Any] = {
        "text": text,
        "correlation_id": "synthetic-docs",
        "external_processor": lambda protected: "Reply: " + protected,
        "handle_blocked": lambda status: pytest.fail("Unexpected rejection"),
        "handle_key_problem": lambda: pytest.fail("Unexpected key-store failure"),
        "handle_unexpected": lambda: pytest.fail("Unexpected public API failure"),
    }
    document = (Path(__file__).resolve().parents[1] / "docs/LIBRARY_API.md").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"```python\n(.*?)\n```", document, re.DOTALL)
    assert len(blocks) == 3
    try:
        for block in blocks:
            exec(compile(block, "<library-reference>", "exec"), namespace)
        assert namespace["restored"].text == "Reply: " + text
        assert namespace["prepared"].text != text
    finally:
        if "prepared" in namespace:
            namespace["gateway"].discard(namespace["prepared"].context)
    assert list(sandbox.iterdir()) == []
