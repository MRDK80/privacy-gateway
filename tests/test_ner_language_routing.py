from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from tools import ner_language_routing
from tools.ner_baseline import Document, Span, score
from tools.ner_bilingual_candidate import Proposal, model_proposals, resolve
from tools.ner_candidate import ModelSpan


@dataclass(frozen=True)
class Entity:
    start_char: int
    end_char: int
    label_: str


@dataclass(frozen=True)
class Prediction:
    ents: Sequence[ModelSpan]


def test_sentence_offsets_preserve_punctuation_unicode_and_repeats() -> None:
    text = "  «Лира…»  Écho?!\r\n \n Écho?!\rЛиния A.B. Я"
    expected = ["«Лира…»", "Écho?!", "Écho?!", "Линия A.B.", "Я"]
    segments = list(ner_language_routing.sentences(text))
    assert [sentence for _, sentence in segments] == expected
    assert [offset for offset, _ in segments] == [2, 11, 22, 29, 40]
    assert all(
        text[offset : offset + len(sentence)] == sentence
        for offset, sentence in segments
    )
    assert list(ner_language_routing.sentences("  \r\n\n  ")) == []


@pytest.mark.parametrize(
    ("text", "indices", "reason"),
    [
        ("Разговор.", (0,), "ru"),
        ("Résumé!", (1,), "en"),
        ("Обсудили Blue сегодня утром на встрече.", (0,), "mixed_ru"),
        ("abcd Я", (1,), "mixed_en"),
        ("Мир Owl", (0, 1), "mixed_both"),
        ("Да.", (0, 1), "short"),
        ("Я A.", (0, 1), "short"),
        ("77 !", (0, 1), "undetermined"),
        ("漢字 Ω.", (0, 1), "undetermined"),
    ],
)
def test_script_route(text: str, indices: tuple[int, ...], reason: str) -> None:
    assert ner_language_routing.sentence_route(text) == (indices, reason)


def test_model_offsets_are_checked_against_the_sentence() -> None:
    def model(_: str) -> Prediction:
        return Prediction([Entity(0, 5, "PER")])

    with pytest.raises(ValueError, match="invalid character offsets"):
        model_proposals("Лира", [(model, {"PER": "PERSON"})], (0,), offset=10)


def test_routed_models_receive_sentences_without_document_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []
    text = "  Лира.\n  Milo Wexfern.  Лира.\n  Мир Owl.\n  box@example.test"

    def russian(sentence: str) -> Prediction:
        seen.append(("ru", sentence))
        return (
            Prediction([Entity(0, 4, "PER")]) if sentence == "Лира." else Prediction([])
        )

    def english(sentence: str) -> Prediction:
        seen.append(("en", sentence))
        if sentence.startswith("Milo") or sentence.startswith("box"):
            return Prediction([Entity(0, len(sentence), "PERSON")])
        return Prediction([])

    monkeypatch.setattr(
        ner_language_routing,
        "load_models",
        lambda: [(russian, {"PER": "PERSON"}), (english, {"PERSON": "PERSON"})],
    )
    annotations = frozenset(
        {
            (2, 6, "PERSON"),
            (text.rindex("Лира"), text.rindex("Лира") + 4, "PERSON"),
            (text.index("Milo"), text.index("Milo") + len("Milo Wexfern"), "PERSON"),
        }
    )

    def check_baseline(proposed: list[Proposal], preserved: list[Span]) -> list[Span]:
        results = resolve(proposed, preserved)
        email_start = text.index("box@example.test")
        assert set(preserved) == {
            (text.index("Milo"), text.index("Milo") + len("Milo Wexfern"), "PERSON"),
            (email_start, email_start + len("box@example.test"), "EMAIL"),
        }
        assert set(preserved) <= set(results)
        return results

    monkeypatch.setattr(ner_language_routing, "resolve", check_baseline)
    # English metadata still lets the Cyrillic sentences select Russian.
    report = ner_language_routing.run(
        [Document("synthetic", "en", text, annotations)], "routed", False
    )
    assert seen == [
        ("ru", "Лира."),
        ("en", "Milo Wexfern."),
        ("ru", "Лира."),
        ("ru", "Мир Owl."),
        ("en", "Мир Owl."),
        ("en", "box@example.test"),
    ]
    assert report["metrics"] == score(
        [(Document("synthetic", "en", text, annotations), annotations)]
    )
    diagnostics = report["diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["discarded_proposals"] == 2


def test_diagnostics_distinguish_prediction_errors_and_merge_losses() -> None:
    gold = frozenset(
        {(0, 5, "ORG"), (8, 12, "PERSON"), (20, 23, "LOC"), (30, 35, "LOC")}
    )
    preserved = [(0, 5, "PERSON")]
    proposals = [
        (0, (0, 5, "ORG")),
        (1, (8, 13, "PERSON")),
        (0, (8, 12, "PERSON")),
        (0, (20, 24, "ORG")),
        (1, (25, 29, "ORG")),
    ]
    combined = set(resolve(proposals, preserved))
    assert ner_language_routing.errors(gold, combined) == {
        "type_only": 1,
        "boundary_only": 1,
        "type_and_boundary": 1,
        "no_gold_overlap": 1,
    }
    assert ner_language_routing.merge_losses(gold, preserved, proposals, combined) == {
        "gold_blocked_by_baseline": 1,
        "gold_blocked_by_model": 1,
        "discarded_proposals": 2,
    }


def test_cli_rejects_changed_corpus_before_loading_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(b"invalid JSON")

    def fail_to_load() -> None:
        pytest.fail("Models must not load before the corpus digest is checked")

    monkeypatch.setattr(ner_language_routing, "load_models", fail_to_load)
    with pytest.raises(ValueError, match="Corpus SHA-256"):
        ner_language_routing.main(["--corpus", str(path), "--variant", "full"])
