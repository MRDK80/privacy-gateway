from tools.ner_baseline import Document
from tools.ner_bilingual_candidate import ceiling, resolve


def test_resolve_preserves_baseline_and_reruns_model_overlap_resolution() -> None:
    proposals = [
        (0, (0, 8, "PERSON")),
        (1, (3, 8, "ORG")),
        (1, (8, 12, "PERSON")),
        (0, (8, 12, "LOC")),
    ]
    assert resolve(proposals) == [(0, 8, "PERSON"), (8, 12, "LOC")]
    assert resolve(proposals, [(0, 3, "EMAIL")]) == [
        (0, 3, "EMAIL"),
        (3, 8, "ORG"),
        (8, 12, "LOC"),
    ]


def test_combined_ceiling_counts_unreachable_gold_and_fixed_fp() -> None:
    corpus = [
        Document(
            "one",
            "ru",
            "12345678",
            frozenset({(0, 3, "PERSON"), (5, 8, "PERSON")}),
        )
    ]
    baseline = [[(0, 2, "EMAIL"), (3, 5, "PERSON")]]
    assert ceiling(corpus, baseline)["ru"]["PERSON"] == {
        "gold": 2,
        "fixed_fp": 1,
        "blocked_gold": 1,
        "max_tp": 1,
        "max_precision": 0.5,
        "max_recall": 0.5,
    }
