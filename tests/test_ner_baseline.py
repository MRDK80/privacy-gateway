from tools.ner_baseline import Document, load_corpus, run, score


def test_ner_score_uses_exact_document_span_and_type() -> None:
    first = Document(
        "one", "ru", "abcdefghi", frozenset({(0, 3, "PERSON"), (4, 7, "ORG")})
    )
    second = Document("two", "ru", "abcdefghi", frozenset({(0, 3, "PERSON")}))
    third = Document("three", "en", "abcdefghi", frozenset())
    metrics = score(
        [
            (first, [(0, 2, "PERSON"), (4, 7, "PERSON"), (4, 7, "PERSON")]),
            (second, [(0, 3, "PERSON"), (7, 8, "EMAIL")]),
            (third, [(4, 7, "LOC")]),
        ]
    )
    assert metrics["ru"]["PERSON"] == {
        "tp": 1,
        "fp": 2,
        "fn": 1,
        "precision": 1 / 3,
        "recall": 0.5,
    }
    assert metrics["ru"]["ORG"] == {
        "tp": 0,
        "fp": 0,
        "fn": 1,
        "precision": None,
        "recall": 0.0,
    }
    assert metrics["en"]["LOC"] == {
        "tp": 0,
        "fp": 1,
        "fn": 0,
        "precision": 0.0,
        "recall": None,
    }


def test_ner_corpus_has_disjoint_documents_and_entity_surfaces() -> None:
    tuning = load_corpus("tuning")
    held_out = load_corpus("held-out")
    assert {doc.text for doc in tuning}.isdisjoint(doc.text for doc in held_out)
    assert {
        doc.text[start:end].casefold() for doc in tuning for start, end, _ in doc.spans
    }.isdisjoint(
        doc.text[start:end].casefold()
        for doc in held_out
        for start, end, _ in doc.spans
    )


def test_ner_baseline_on_held_out_corpus() -> None:
    expected = {
        "PERSON": {"tp": 1, "fp": 3, "fn": 5, "precision": 0.25, "recall": 1 / 6},
        "ORG": {"tp": 1, "fp": 1, "fn": 3, "precision": 0.5, "recall": 0.25},
        "LOC": {"tp": 0, "fp": 0, "fn": 4, "precision": None, "recall": 0.0},
    }
    assert run("held-out", timing=False) == {
        "split": "held-out",
        "documents": 18,
        "metrics": {"ru": expected, "en": expected},
    }
