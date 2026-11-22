"""Score the current detector on the frozen synthetic NER corpus."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from time import perf_counter_ns, process_time_ns

from privacy_gateway.detector import detect_entities, load_config

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ner"
SPLITS = ("tuning", "held-out")
LANGUAGES = ("ru", "en")
TYPES = ("PERSON", "ORG", "LOC")

Span = tuple[int, int, str]


@dataclass(frozen=True)
class Document:
    id: str
    language: str
    text: str
    spans: frozenset[Span]


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, gold: set[Span], predicted: set[Span]) -> None:
        self.tp += len(gold & predicted)
        self.fp += len(predicted - gold)
        self.fn += len(gold - predicted)

    def report(self) -> dict[str, int | float | None]:
        predicted = self.tp + self.fp
        gold = self.tp + self.fn
        return {
            **asdict(self),
            "precision": self.tp / predicted if predicted else None,
            "recall": self.tp / gold if gold else None,
        }


def load_corpus(split: str) -> list[Document]:
    documents = []
    seen_ids = set()
    path = FIXTURES / f"{split}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        spans: set[Span] = set()
        for start, end, label in row["spans"]:
            if not 0 <= start < end <= len(row["text"]) or label not in TYPES:
                raise ValueError(f"Invalid span in {split}/{row['id']}")
            spans.add((start, end, label))
        if len(spans) != len(row["spans"]):
            raise ValueError(f"Duplicate span in {split}/{row['id']}")
        if row["language"] not in LANGUAGES or row["id"] in seen_ids:
            raise ValueError(f"Invalid document language or ID in {split}/{row['id']}")
        seen_ids.add(row["id"])
        documents.append(
            Document(row["id"], row["language"], row["text"], frozenset(spans))
        )
    return documents


def score(
    examples: Iterable[tuple[Document, Iterable[Span]]],
) -> dict[str, dict[str, dict[str, int | float | None]]]:
    totals = {(language, label): Counts() for language in LANGUAGES for label in TYPES}
    for document, predictions in examples:
        predicted = set(predictions)
        for label in TYPES:
            totals[document.language, label].add(
                {span for span in document.spans if span[2] == label},
                {span for span in predicted if span[2] == label},
            )
    return {
        language: {label: totals[language, label].report() for label in TYPES}
        for language in LANGUAGES
    }


def run(split: str, timing: bool) -> dict[str, object]:
    documents = load_corpus(split)
    config = load_config(FIXTURES / "dictionary.yaml")
    report: dict[str, object] = {
        "split": split,
        "documents": len(documents),
        "metrics": score(
            (
                document,
                (
                    (item.start, item.end, item.entity_type.value)
                    for item in detect_entities(document.text, config)
                ),
            )
            for document in documents
        ),
    }
    if timing:
        samples = []
        cpu_start = process_time_ns()
        for _ in range(100):
            for document in documents:
                start = perf_counter_ns()
                detect_entities(document.text, config)
                samples.append(perf_counter_ns() - start)
        cpu_ns = process_time_ns() - cpu_start
        samples.sort()
        report["timing"] = {
            "calls": len(samples),
            "mean_cpu_ms": cpu_ns / len(samples) / 1_000_000,
            "p95_wall_ms": samples[ceil(0.95 * len(samples)) - 1] / 1_000_000,
        }
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=SPLITS, default="held-out")
    parser.add_argument(
        "--timing", action="store_true", help="Time 100 warm corpus passes"
    )
    args = parser.parse_args(argv)
    print(json.dumps(run(args.split, args.timing), indent=2))


if __name__ == "__main__":
    main()
