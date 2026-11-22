from pathlib import Path

import pytest
from tools.ner_baseline import Document
from tools.ner_finetune import main, model_digests, training_examples
from tools.ner_finetune_corpus import documents, render


def test_renderer_preserves_legal_designation_and_repeated_offsets() -> None:
    text, spans = render(
        "Через ООО «{org_core}». Имя: {name}. Ещё: {name}.",
        {"org_core": ("Ивный круг", "ORG"), "name": ("Сувир Крел", "PERSON")},
    )
    assert [(text[begin:end], label) for begin, end, label in spans] == [
        ("ООО «Ивный круг»", "ORG"),
        ("Сувир Крел", "PERSON"),
        ("Сувир Крел", "PERSON"),
    ]
    assert spans[1][0] != spans[2][0]


def test_corpus_splits_are_unique_and_deterministic() -> None:
    train, dev = (list(documents(split)) for split in ("train", "dev"))
    assert (len(train), len(dev)) == (326, 102)
    train_texts = {row["text"] for row in train}
    dev_texts = {row["text"] for row in dev}
    assert len(train_texts) == len(train)
    assert len(dev_texts) == len(dev)
    assert not train_texts & dev_texts
    assert train == list(documents("train"))


def test_training_uses_sentence_route_and_native_labels() -> None:
    text = "  Зорян пришёл.\n Gremduff is nearby."
    english_start = text.index("Gremduff")
    document = Document(
        "sample",
        "en",
        text,
        frozenset({(2, 7, "PERSON"), (english_start, english_start + 8, "LOC")}),
        mixed=True,
    )
    assert training_examples([document], 0) == [("Зорян пришёл.", [(0, 5, "PER")])]
    assert training_examples([document], 1) == [
        ("Gremduff is nearby.", [(0, 8, "GPE")])
    ]


def test_training_fails_on_span_crossing_sentence_boundary() -> None:
    document = Document("sample", "en", "Foo! Bar.", frozenset({(0, 8, "ORG")}))
    with pytest.raises(ValueError, match="crosses a sentence"):
        training_examples([document], 1)


def test_evaluator_rejects_model_fingerprint_mismatch_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from tools import ner_finetune

    corpus = tmp_path / "corpus.jsonl"
    raw = b'{"id": "1", "text": "Ping", "language": "en", "spans": []}\n'
    corpus.write_bytes(raw)
    models = tmp_path / "models"
    for model in ("ru_core_news_sm", "en_core_web_sm"):
        directory = models / model
        directory.mkdir(parents=True)
        (directory / "data").write_bytes(b"initial synthetic model marker")
    digests = model_digests(models)
    (models / "ru_core_news_sm" / "data").write_bytes(b"modified synthetic marker")

    def unexpected_run() -> None:
        pytest.fail("Inference ran on an unverified model")

    monkeypatch.setattr(ner_finetune, "run", unexpected_run)
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "evaluate",
                "--configuration",
                "trained",
                "--corpus",
                str(corpus),
                "--expect-sha256",
                hashlib.sha256(raw).hexdigest(),
                "--models",
                str(models),
                "--expect-model-sha256",
                *digests.values(),
            ]
        )
