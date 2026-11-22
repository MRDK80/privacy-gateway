"""Train and evaluate #446's offline spaCy NER candidate."""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from hashlib import sha256
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from tools.ner_baseline import Document, Span, read_corpus
from tools.ner_bilingual_candidate import COMPONENTS, MODELS, Model, load_models
from tools.ner_candidate import RUNTIME_VERSION
from tools.ner_language_routing import run, sentence_route, sentences

SEED = 446
EPOCHS = 8
BATCH_SIZE = 8
DROPOUT = 0.1
NATIVE_LABELS = (
    {"PERSON": "PER", "ORG": "ORG", "LOC": "LOC"},
    {"PERSON": "PERSON", "ORG": "ORG", "LOC": "GPE"},
)


def model_digests(root: Path) -> dict[str, str]:
    digests = {}
    for package, _, _ in MODELS:
        directory = root / package
        if not directory.is_dir():
            raise ValueError("Missing trained model directory.")
        hashes = {
            path.relative_to(directory).as_posix(): sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }
        manifest = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
        digests[package] = sha256(manifest.encode()).hexdigest()
    return digests


def training_examples(
    corpus: Sequence[Document], source: int
) -> list[tuple[str, list[Span]]]:
    examples = []
    for document in corpus:
        covered: set[Span] = set()
        for offset, sentence in sentences(document.text):
            spans = {
                span
                for span in document.spans
                if offset <= span[0] < span[1] <= offset + len(sentence)
            }
            covered.update(spans)
            if source in sentence_route(sentence)[0]:
                examples.append(
                    (
                        sentence,
                        sorted(
                            (start - offset, end - offset, NATIVE_LABELS[source][label])
                            for start, end, label in spans
                        ),
                    )
                )
        if covered != document.spans:
            raise ValueError("Training annotation crosses a sentence boundary.")
    return examples


def train(corpus: Sequence[Document], output: Path) -> dict[str, object]:
    models = load_models()
    fix_random_seed = import_module("spacy.util").fix_random_seed
    example_type = import_module("spacy.training").Example
    output.mkdir(parents=True)
    examples_by_model = {}
    for source, ((model, _), (package, _, _)) in enumerate(
        zip(models, MODELS, strict=True)
    ):
        # spaCy is an optional offline dependency, accessed through its runtime API.
        nlp: Any = model
        seed = SEED + source
        fix_random_seed(seed)
        order = random.Random(seed)
        examples = []
        for sentence, spans in training_examples(corpus, source):
            doc = nlp.make_doc(sentence)
            if any(doc.char_span(start, end) is None for start, end, _ in spans):
                raise ValueError("Training annotation is not token aligned.")
            examples.append(example_type.from_dict(doc, {"entities": spans}))
        optimizer = nlp.resume_training()
        for _ in range(EPOCHS):
            order.shuffle(examples)
            for start in range(0, len(examples), BATCH_SIZE):
                nlp.update(
                    examples[start : start + BATCH_SIZE], sgd=optimizer, drop=DROPOUT
                )
        nlp.to_disk(output / package)
        examples_by_model[package] = len(examples)
    return {
        "seed": SEED,
        "epochs": EPOCHS,
        "batch_size": BATCH_SIZE,
        "dropout": DROPOUT,
        "training_sentences": examples_by_model,
        "model_sha256": model_digests(output),
    }


def load_trained_models(root: Path) -> list[tuple[Model, dict[str, str]]]:
    if version("spacy") != RUNTIME_VERSION:
        raise RuntimeError("The benchmark requires the pinned spaCy version.")
    spacy = import_module("spacy")
    spacy.require_cpu()
    models = []
    for package, _, labels in MODELS:
        nlp = spacy.load(root / package)
        if tuple(nlp.pipe_names) != COMPONENTS:
            raise RuntimeError(f"Unexpected loaded components in {package}.")
        if not labels.keys() <= set(nlp.get_pipe("ner").labels):
            raise RuntimeError(f"Expected NER labels are missing from {package}.")
        models.append((cast(Model, nlp), labels))
    return models


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--output", required=True, type=Path)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument(
        "--configuration", required=True, choices=("full", "routed", "trained")
    )
    evaluate_parser.add_argument("--models", type=Path)
    evaluate_parser.add_argument("--expect-model-sha256", nargs=2, metavar=("RU", "EN"))
    evaluate_parser.add_argument(
        "--timing", action="store_true", help="Time 100 warm corpus passes"
    )
    for command in (train_parser, evaluate_parser):
        command.add_argument("--corpus", required=True, type=Path)
        command.add_argument("--expect-sha256", required=True)
    args = parser.parse_args(argv)
    corpus = read_corpus(args.corpus, expected_sha256=args.expect_sha256)
    if not corpus:
        parser.error("The corpus is empty.")
    report: dict[str, object] = {"corpus_sha256": args.expect_sha256}
    if args.command == "train":
        report.update(train(corpus, args.output))
    else:
        loader = None
        if args.configuration == "trained":
            if args.models is None or args.expect_model_sha256 is None:
                parser.error(
                    "The trained configuration requires model paths and digests."
                )
            expected = dict(
                zip(
                    (package for package, _, _ in MODELS),
                    args.expect_model_sha256,
                    strict=True,
                )
            )
            models_root: Path = args.models
            if model_digests(models_root) != expected:
                parser.error("Trained model SHA-256 differs from the expected digests.")

            def loader() -> list[tuple[Model, dict[str, str]]]:
                return load_trained_models(models_root)

            report["model_sha256"] = expected
        elif args.models is not None or args.expect_model_sha256 is not None:
            parser.error(
                "Model paths and digests are only for the trained configuration."
            )
        variant = "full" if args.configuration == "full" else "routed"
        report["configuration"] = args.configuration
        report.update(
            run(
                corpus,
                variant,
                args.timing,
                models_loader=loader,
                include_model_only=True,
            )
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
