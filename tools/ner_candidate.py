"""Evaluate one optional spaCy model on the synthetic NER benchmark."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from importlib import import_module
from importlib.metadata import version
from math import ceil
from time import perf_counter_ns, process_time_ns
from typing import Protocol, cast

from privacy_gateway.detector import detect_entities, load_config
from tools.ner_baseline import FIXTURES, SPLITS, Span, load_corpus, score

MODEL = "xx_ent_wiki_sm"
MODEL_VERSION = "3.8.0"
RUNTIME_VERSION = "3.8.18"
LABELS = {"PER": "PERSON", "ORG": "ORG", "LOC": "LOC"}


class ModelSpan(Protocol):
    @property
    def start_char(self) -> int: ...
    @property
    def end_char(self) -> int: ...
    @property
    def label_(self) -> str: ...


class ModelDocument(Protocol):
    @property
    def ents(self) -> Sequence[ModelSpan]: ...


def load_model() -> Callable[[str], ModelDocument]:
    if version("spacy") != RUNTIME_VERSION or version(MODEL) != MODEL_VERSION:
        raise RuntimeError(
            "The benchmark requires the pinned spaCy and model versions."
        )
    import_module("spacy").require_cpu()
    return cast(Callable[[str], ModelDocument], import_module(MODEL).load())


def run(split: str, timing: bool) -> dict[str, object]:
    documents = load_corpus(split)
    config = load_config(FIXTURES / "dictionary.yaml")

    start = perf_counter_ns()
    model = load_model()
    model_load_ms = (perf_counter_ns() - start) / 1_000_000

    def detect(text: str) -> list[Span]:
        baseline = [
            (item.start, item.end, item.entity_type.value)
            for item in detect_entities(text, config)
        ]
        added: list[Span] = []
        for entity in model(text).ents:
            start, end = entity.start_char, entity.end_char
            if not 0 <= start < end <= len(text):
                raise ValueError("Model returned invalid character offsets.")
            label = LABELS.get(entity.label_)
            if label is None:
                continue
            if any(start < right and left < end for left, right, _ in baseline):
                continue
            added.append((start, end, label))
        return baseline + added

    start = perf_counter_ns()
    first = detect(documents[0].text)
    first_call_ms = (perf_counter_ns() - start) / 1_000_000
    predictions = [first, *(detect(document.text) for document in documents[1:])]
    report: dict[str, object] = {
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "spacy_version": RUNTIME_VERSION,
        "split": split,
        "documents": len(documents),
        "metrics": score(zip(documents, predictions, strict=True)),
    }
    if timing:
        samples = []
        cpu_start = process_time_ns()
        for _ in range(100):
            for document in documents:
                start = perf_counter_ns()
                detect(document.text)
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
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument(
        "--timing", action="store_true", help="Time 100 warm corpus passes"
    )
    args = parser.parse_args(argv)
    print(json.dumps(run(args.split, args.timing), indent=2))


if __name__ == "__main__":
    main()
