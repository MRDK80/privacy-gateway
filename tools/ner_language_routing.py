"""Compare full-document, bilingual sentence and script-routed NER offline."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from math import ceil
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from unicodedata import name, unidata_version

from privacy_gateway.detector import detect_entities, load_config
from tools.ner_baseline import FIXTURES, TYPES, Document, Span, read_corpus, score
from tools.ner_bilingual_candidate import (
    MODEL_VERSION,
    MODELS,
    Proposal,
    load_models,
    model_proposals,
    overlaps,
    resolve,
)
from tools.ner_candidate import RUNTIME_VERSION

VARIANTS = ("full", "both", "routed")
# Published SHA-256 of the synthetic development corpus in #445.
CORPUS_SHA256 = (
    # pragma: allowlist nextline secret
    "c61f248db8337632d72682feb9b84ad4ade36eb78010f60b8d28888ccfdd62c6"
)
# The lookahead leaves whitespace for trimming and keeps the punctuation.
BOUNDARY = re.compile(r"""[.!?…]+["'”’»\)\]\}]*(?=\s|$)|\r\n|[\r\n]""")
BOTH_MODELS: tuple[int, ...] = (0, 1)


def sentences(text: str) -> Iterator[tuple[int, str]]:
    start = 0
    for boundary in BOUNDARY.finditer(text):
        end = boundary.end()
        sentence = text[start:end].lstrip()
        if sentence.strip():
            yield end - len(sentence), sentence.rstrip()
        start = end
    sentence = text[start:].lstrip()
    if sentence:
        yield len(text) - len(sentence), sentence.rstrip()


def sentence_route(text: str) -> tuple[tuple[int, ...], str]:
    cyrillic = latin = 0
    for char in text:
        if char.isalpha():
            script = name(char, "").partition(" ")[0]
            cyrillic += script == "CYRILLIC"
            latin += script == "LATIN"
    total = cyrillic + latin
    if total == 0:
        return BOTH_MODELS, "undetermined"
    if total < 4:
        return BOTH_MODELS, "short"
    if 5 * cyrillic >= 4 * total:
        return (0,), "mixed_ru" if latin else "ru"
    if 5 * latin >= 4 * total:
        return (1,), "mixed_en" if cyrillic else "en"
    return BOTH_MODELS, "mixed_both"


def errors(gold: frozenset[Span], predicted: set[Span]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for start, end, label in predicted - gold:
        if any((left, right) == (start, end) for left, right, _ in gold):
            counts["type_only"] += 1
        elif overlaps((start, end, label), (item for item in gold if item[2] == label)):
            counts["boundary_only"] += 1
        elif overlaps((start, end, label), gold):
            counts["type_and_boundary"] += 1
        else:
            counts["no_gold_overlap"] += 1
    return counts


def merge_losses(
    gold: frozenset[Span],
    baseline: Sequence[Span],
    proposed: Sequence[Proposal],
    combined: set[Span],
) -> Counter[str]:
    losses: Counter[str] = Counter()
    missing = gold & {span for _, span in proposed} - combined
    for span in missing:
        cause = (
            "gold_blocked_by_baseline"
            if overlaps(span, baseline)
            else "gold_blocked_by_model"
        )
        losses[cause] += 1
    losses["discarded_proposals"] = len(proposed) - (len(combined) - len(baseline))
    return losses


def run(corpus: Sequence[Document], variant: str, timing: bool) -> dict[str, object]:
    config = load_config(FIXTURES / "dictionary.yaml")
    start = perf_counter_ns()
    models = load_models()
    model_load_ms = (perf_counter_ns() - start) / 1_000_000

    def detect(
        text: str,
    ) -> tuple[list[Span], list[Proposal], list[Span], Counter[str]]:
        baseline = [
            (item.start, item.end, item.entity_type.value)
            for item in detect_entities(text, config)
        ]
        routes: Counter[str] = Counter()
        if variant == "full":
            proposed = model_proposals(text, models, BOTH_MODELS)
        else:
            proposed = []
            for offset, sentence in sentences(text):
                sources = BOTH_MODELS
                if variant == "routed":
                    sources, choice = sentence_route(sentence)
                    routes[choice] += 1
                proposed.extend(model_proposals(sentence, models, sources, offset))
        return baseline, proposed, resolve(proposed, baseline), routes

    # One complete untimed pass, with quality diagnostics kept out of the timed loop.
    examples: list[tuple[Document, list[Span]]] = []
    routes: Counter[str] = Counter(
        dict.fromkeys(
            ("ru", "en", "short", "undetermined", "mixed_ru", "mixed_en", "mixed_both"),
            0,
        )
    )
    diagnostics: Counter[str] = Counter(
        dict.fromkeys(
            (
                "type_only",
                "boundary_only",
                "type_and_boundary",
                "no_gold_overlap",
                "gold_blocked_by_baseline",
                "gold_blocked_by_model",
                "discarded_proposals",
            ),
            0,
        )
    )
    for document in corpus:
        baseline, proposed, combined, choices = detect(document.text)
        examples.append((document, combined))
        scored = {span for span in combined if span[2] in TYPES}
        diagnostics.update(errors(document.spans, scored))
        diagnostics.update(
            merge_losses(document.spans, baseline, proposed, set(combined))
        )
        routes.update(choices)

    report: dict[str, object] = {
        "variant": variant,
        "models": {package: MODEL_VERSION for package, _, _ in MODELS},
        "spacy_version": RUNTIME_VERSION,
        "unicode_version": unidata_version,
        "documents": {
            "all": len(corpus),
            "monolingual": sum(not document.mixed for document in corpus),
            "mixed": sum(document.mixed for document in corpus),
        },
        "metrics": score(examples),
        "monolingual_metrics": score(
            example for example in examples if not example[0].mixed
        ),
        "mixed_metrics": score(example for example in examples if example[0].mixed),
        "diagnostics": diagnostics,
    }
    if variant == "routed":
        report["routing"] = {
            "sentences": routes.total(),
            "uncertain": routes.total() - routes["ru"] - routes["en"],
            "choices": routes,
        }
    if timing:
        samples = []
        cpu_start = process_time_ns()
        for _ in range(100):
            for document in corpus:
                start = perf_counter_ns()
                detect(document.text)
                samples.append(perf_counter_ns() - start)
        cpu_ns = process_time_ns() - cpu_start
        samples.sort()
        report["timing"] = {
            "calls": len(samples),
            "model_load_ms": model_load_ms,
            "mean_cpu_ms": cpu_ns / len(samples) / 1_000_000,
            "p95_wall_ms": samples[ceil(0.95 * len(samples)) - 1] / 1_000_000,
        }
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--variant", required=True, choices=VARIANTS)
    parser.add_argument(
        "--timing", action="store_true", help="Time 100 warm corpus passes"
    )
    args = parser.parse_args(argv)
    corpus = read_corpus(args.corpus, expected_sha256=CORPUS_SHA256)
    print(
        json.dumps(
            {"corpus_sha256": CORPUS_SHA256, **run(corpus, args.variant, args.timing)},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
