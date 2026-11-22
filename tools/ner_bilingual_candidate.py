"""Run the preregistered NER pair on a supplied, digest-verified synthetic corpus."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Sequence
from importlib import import_module
from importlib.metadata import version
from math import ceil
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import cast

from privacy_gateway.detector import detect_entities, load_config
from tools.ner_baseline import (
    FIXTURES,
    LANGUAGES,
    TYPES,
    Document,
    Span,
    read_corpus,
    score,
)
from tools.ner_candidate import RUNTIME_VERSION, ModelDocument

MODEL_VERSION = "3.8.0"
COMPONENTS = ("tok2vec", "ner")
MODELS = (
    (
        "ru_core_news_sm",
        ("morphologizer", "parser", "senter", "attribute_ruler", "lemmatizer"),
        {"PER": "PERSON", "ORG": "ORG", "LOC": "LOC"},
    ),
    (
        "en_core_web_sm",
        ("tagger", "parser", "senter", "attribute_ruler", "lemmatizer"),
        {"PERSON": "PERSON", "ORG": "ORG", "LOC": "LOC", "GPE": "LOC"},
    ),
)

Model = Callable[[str], ModelDocument]
Proposal = tuple[int, Span]


def load_models() -> list[tuple[Model, dict[str, str]]]:
    if version("spacy") != RUNTIME_VERSION:
        raise RuntimeError("The benchmark requires the pinned spaCy version.")
    import_module("spacy").require_cpu()
    models = []
    for package, excluded, labels in MODELS:
        if version(package) != MODEL_VERSION:
            raise RuntimeError(f"The benchmark requires the pinned {package} version.")
        nlp = import_module(package).load(exclude=excluded)
        if tuple(nlp.pipe_names) != COMPONENTS:
            raise RuntimeError(f"Unexpected loaded components in {package}.")
        if not labels.keys() <= set(nlp.get_pipe("ner").labels):
            raise RuntimeError(f"Expected NER labels are missing from {package}.")
        models.append((cast(Model, nlp), labels))
    return models


def overlaps(span: Span, others: Iterable[Span]) -> bool:
    start, end, _ = span
    return any(start < right and left < end for left, right, _ in others)


def order(proposal: Proposal) -> tuple[int, int, int, str]:
    source, (start, end, label) = proposal
    return (start - end, start, source, label)


def resolve(
    proposals: Iterable[Proposal], preserved: Iterable[Span] = ()
) -> list[Span]:
    accepted = list(preserved)
    for _, span in sorted(proposals, key=order):
        if not overlaps(span, accepted):
            accepted.append(span)
    return accepted


def ceiling(
    corpus: Sequence[Document], baseline: Sequence[list[Span]]
) -> dict[str, dict[str, dict[str, int | float | None]]]:
    rows: dict[str, dict[str, dict[str, int | float | None]]] = {}
    for language in LANGUAGES:
        rows[language] = {}
        for label in TYPES:
            gold = fixed_fp = blocked = 0
            for document, predicted in zip(corpus, baseline, strict=True):
                if document.language != language:
                    continue
                expected = {span for span in document.spans if span[2] == label}
                relevant = {span for span in predicted if span[2] == label}
                gold += len(expected)
                fixed_fp += len(relevant - expected)
                blocked += sum(
                    overlaps(span, predicted) for span in expected - relevant
                )
            reachable = gold - blocked
            possible = reachable + fixed_fp
            rows[language][label] = {
                "gold": gold,
                "fixed_fp": fixed_fp,
                "blocked_gold": blocked,
                "max_tp": reachable,
                "max_precision": reachable / possible if possible else None,
                "max_recall": reachable / gold if gold else None,
            }
    return rows


def run(corpus: Sequence[Document], timing: bool) -> dict[str, object]:
    config = load_config(FIXTURES / "dictionary.yaml")
    start = perf_counter_ns()
    models = load_models()
    model_load_ms = (perf_counter_ns() - start) / 1_000_000

    def detect(text: str) -> tuple[list[Span], list[Proposal]]:
        baseline = [
            (item.start, item.end, item.entity_type.value)
            for item in detect_entities(text, config)
        ]
        proposals = []
        for source, (model, labels) in enumerate(models):
            for entity in model(text).ents:
                start, end = entity.start_char, entity.end_char
                if not 0 <= start < end <= len(text):
                    raise ValueError("Model returned invalid character offsets.")
                label = labels.get(entity.label_)
                if label is not None:
                    proposals.append((source, (start, end, label)))
        return baseline, proposals

    start = perf_counter_ns()
    first_baseline, first_proposals = detect(corpus[0].text)
    first_combined = resolve(first_proposals, first_baseline)
    first_call_ms = (perf_counter_ns() - start) / 1_000_000
    baseline = [first_baseline]
    model_only = [resolve(first_proposals)]
    combined = [first_combined]
    for document in corpus[1:]:
        predictions, proposals = detect(document.text)
        baseline.append(predictions)
        model_only.append(resolve(proposals))
        combined.append(resolve(proposals, predictions))

    modes = {"baseline": baseline, "model_only": model_only, "combined": combined}
    report: dict[str, object] = {
        "models": {package: MODEL_VERSION for package, _, _ in MODELS},
        "spacy_version": RUNTIME_VERSION,
        "documents": len(corpus),
        "mixed_documents": sum(document.mixed for document in corpus),
        "metrics": {
            mode: score(zip(corpus, spans, strict=True))
            for mode, spans in modes.items()
        },
        "mixed_metrics": {
            mode: score(
                (document, predictions)
                for document, predictions in zip(corpus, spans, strict=True)
                if document.mixed
            )
            for mode, spans in modes.items()
        },
        "combined_ceiling": ceiling(corpus, baseline),
    }
    if timing:
        samples = []
        cpu_start = process_time_ns()
        for _ in range(100):
            for document in corpus:
                start = perf_counter_ns()
                predictions, proposals = detect(document.text)
                resolve(proposals, predictions)
                samples.append(perf_counter_ns() - start)
        cpu_ns = process_time_ns() - cpu_start
        samples.sort()
        report["timing"] = {
            "calls": len(samples),
            "model_load_ms": model_load_ms,
            "first_call_ms": first_call_ms,
            "mean_cpu_ms": cpu_ns / len(samples) / 1_000_000,
            "p95_wall_ms": samples[ceil(0.95 * len(samples)) - 1] / 1_000_000,
        }
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--expect-sha256", required=True)
    parser.add_argument("--timing", action="store_true", help="Time 100 warm passes")
    args = parser.parse_args(argv)
    corpus = read_corpus(args.corpus, expected_sha256=args.expect_sha256)
    if not corpus:
        parser.error("The corpus is empty.")
    print(
        json.dumps(
            {"corpus_sha256": args.expect_sha256, **run(corpus, args.timing)}, indent=2
        )
    )


if __name__ == "__main__":
    main()
