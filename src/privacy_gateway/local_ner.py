"""Offline runtime for the NER configuration selected in ADR-473."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from hashlib import file_digest, sha256
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from typing import Any
from unicodedata import name

from privacy_gateway.models import ConfigurationError

Span = tuple[int, int, str]
LABELS = ("PERSON", "ORG", "LOC")
PROMPT = (
    "<<ENT>>",
    "person",
    "<<ENT>>",
    "organization",
    "<<ENT>>",
    "location",
    "<<SEP>>",
)
CAPITALIZED_WORDS = re.compile(r"[^\W\d_]+")
WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")
BOUNDARY = re.compile(r"""[.!?…]+["'”’»\)\]\}]*(?=\s|$)|\r\n|[\r\n]""")
WIDTH = 12
MAX_WORDS = 384
MAX_SUBTOKENS = 512
RECHECK_CUTOFF = 1.5
MODEL_DIGESTS = {
    # pragma: allowlist nextline secret
    "Gather.onnx": "d28ee7b6ec5dbf3713ba3bb64f754961ca848fa79807f9d77ec5b5fce8763f89",
    # pragma: allowlist nextline secret
    "tokenizer.json": (
        # pragma: allowlist nextline secret
        "c5b8041501fcdee792b9b112dd592861f04391a24633a73fd9f05aaaf6e8eff1"
    ),
}
SPACY_MODELS = (
    (
        "ru_core_news_sm",
        ("morphologizer", "parser", "senter", "attribute_ruler", "lemmatizer"),
        {"PER": "PERSON", "ORG": "ORG", "LOC": "LOC"},
        # pragma: allowlist nextline secret
        "bda6fb158819cc28e0d9e61aefdba8da21aa0e17271d3bc3135fbcb3bd8db2f2",
    ),
    (
        "en_core_web_sm",
        ("tagger", "parser", "senter", "attribute_ruler", "lemmatizer"),
        {"PERSON": "PERSON", "ORG": "ORG", "LOC": "LOC", "GPE": "LOC"},
        # pragma: allowlist nextline secret
        "7eb475cc0deb930f03262d56c8cd0024844b4bd2451b9e08aba86f0339d38649",
    ),
)


def _file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _tree_hash(path: Path) -> str:
    digest = sha256()
    for item in sorted(
        path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()
    ):
        if item.is_file():
            relative = item.relative_to(path).as_posix()
            digest.update(f"{relative} {_file_hash(item)}\n".encode())
    return digest.hexdigest()


def _sentences(text: str) -> Iterator[tuple[int, str]]:
    start = 0
    for stop in BOUNDARY.finditer(text):
        end = stop.end()
        sentence = text[start:end].lstrip()
        if sentence:
            yield end - len(sentence), sentence.rstrip()
        start = end
    sentence = text[start:].lstrip()
    if sentence:
        yield len(text) - len(sentence), sentence.rstrip()


def _script_counts(text: str) -> tuple[int, int]:
    cyrillic = latin = 0
    for char in text:
        if char.isalpha():
            script = name(char, "").partition(" ")[0]
            cyrillic += script == "CYRILLIC"
            latin += script == "LATIN"
    return cyrillic, latin


def _route(text: str) -> tuple[int, ...]:
    cyrillic, latin = _script_counts(text)
    total = cyrillic + latin
    if total < 4:
        return (0, 1)
    if 5 * cyrillic >= 4 * total:
        return (0,)
    if 5 * latin >= 4 * total:
        return (1,)
    return (0, 1)


def _quoted_extension(sentence: str, start: int, end: int) -> bool:
    if end >= len(sentence) or sentence[end] != "»":
        return False
    depth = 0
    for char in sentence[start:end]:
        if char == "«":
            depth += 1
        elif char == "»":
            depth -= 1
            if depth < 0:
                return False
    return depth == 1


def _contains(outer: Span, inner: Span) -> bool:
    return outer != inner and outer[0] <= inner[0] and inner[1] <= outer[1]


def _overlaps(left: Span, right: Span) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _supported(text: str, offset: int, full: Span, fragments: set[Span]) -> bool:
    start, end, _ = full
    covered = {
        i
        for part in fragments
        if _contains(full, part)
        for i in range(part[0], part[1])
        if not text[i - offset].isspace()
    }
    total = sum(not char.isspace() for char in text[start - offset : end - offset])
    return bool(total and 4 * len(covered) >= total)


def _org_additions(
    sentence: str, offset: int, scored: set[Span], spots: set[Span], matched: set[Span]
) -> set[Span]:
    disputed_orgs = {item for item in spots if item[2] == "ORG"}
    confirmed_orgs = {item for item in matched if item[2] == "ORG"}
    return {
        full
        for full in disputed_orgs - confirmed_orgs
        if _supported(sentence, offset, full, confirmed_orgs)
    } | {
        full
        for full in confirmed_orgs - scored
        if _supported(sentence, offset, full, disputed_orgs)
    }


class _Engine:
    def __init__(self, model_dir: Path) -> None:
        if any(
            version(pkg) != expected
            for pkg, expected in (
                ("onnxruntime", "1.23.2"),
                ("tokenizers", "0.22.2"),
                ("spacy", "3.8.18"),
            )
        ):
            raise ValueError("Unsupported local NER runtime.")
        for filename, digest in MODEL_DIGESTS.items():
            if _file_hash(model_dir / filename) != digest:
                raise ValueError("Local NER model digest mismatch.")
        self._packages: list[Any] = []
        self._models: dict[int, Any] = {}
        for package, _, _, digest in SPACY_MODELS:
            if version(package) != "3.8.0":
                raise ValueError("Unsupported local recheck model.")
            module = import_module(package)
            location = module.__file__
            if location is None:
                raise ValueError("Local recheck model has no installation path.")
            directory = Path(location).parent / f"{package}-3.8.0"
            if _tree_hash(directory) != digest:
                raise ValueError("Local recheck model digest mismatch.")
            self._packages.append(module)
        import_module("spacy").require_cpu()
        self._np = import_module("numpy")
        ort = import_module("onnxruntime")
        self._tokenizer = import_module("tokenizers").Tokenizer.from_file(
            str(model_dir / "tokenizer.json")
        )
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 4
        self._session = ort.InferenceSession(
            str(model_dir / "Gather.onnx"),
            options,
            providers=["CPUExecutionProvider"],
            enable_fallback=False,
        )
        self._inputs = {item.name for item in self._session.get_inputs()}
        self._lock = Lock()

    def _gliner(self, text: str) -> dict[Span, float]:
        np = self._np
        matches = list(WORD.finditer(text))
        proposals: dict[Span, float] = {}
        start = 0
        while start < len(matches):
            size = min(MAX_WORDS, len(matches) - start)
            while True:
                words = [match.group() for match in matches[start : start + size]]
                encoding = self._tokenizer.encode(
                    [*PROMPT, *words], is_pretokenized=True
                )
                if len(encoding.ids) <= MAX_SUBTOKENS:
                    break
                if size == 1:
                    raise ValueError("NER input exceeds its tokenizer window.")
                size = max(1, size // 2)

            mask = []
            previous = None
            for word in encoding.word_ids:
                mask.append(
                    word - len(PROMPT) + 1
                    if word is not None and word >= len(PROMPT) and word != previous
                    else 0
                )
                previous = word
            starts = np.repeat(np.arange(size, dtype=np.int64), WIDTH)
            ends = starts + np.tile(np.arange(WIDTH, dtype=np.int64), size)
            valid = ends < size
            indices = np.stack((starts, ends), axis=-1)
            indices[~valid] = 0
            feeds = {
                "input_ids": np.asarray([encoding.ids], dtype=np.int64),
                "attention_mask": np.asarray([encoding.attention_mask], dtype=np.int64),
                "words_mask": np.asarray([mask], dtype=np.int64),
                "text_lengths": np.asarray([[size]], dtype=np.int64),
                "span_idx": indices[None],
                "span_mask": valid[None],
            }
            (logits,) = self._session.run(
                None, {key: feeds[key] for key in self._inputs}
            )
            if (
                logits.shape != (1, size, WIDTH, len(LABELS))
                or not np.isfinite(logits).all()
            ):
                raise ValueError("Invalid NER output.")
            for left, width, label in np.argwhere(logits[0] > 0):
                right = left + width
                if right < size:
                    span = (
                        matches[start + left].start(),
                        matches[start + right].end(),
                        LABELS[label],
                    )
                    score = float(logits[0, left, width, label])
                    proposals[span] = max(proposals.get(span, 0.0), score)
            if start + size == len(matches):
                break
            start += max(1, size - WIDTH + 1)
        return proposals

    def _recheck(
        self, sentence: str, offset: int, routes: tuple[int, ...]
    ) -> set[Span]:
        found = set()
        for route in routes:
            _, excluded, labels, _ = SPACY_MODELS[route]
            if route not in self._models:
                model = self._packages[route].load(exclude=excluded)
                if tuple(model.pipe_names) != ("tok2vec", "ner"):
                    raise ValueError("Invalid local recheck pipeline.")
                if not labels.keys() <= set(model.get_pipe("ner").labels):
                    raise ValueError("Invalid local recheck labels.")
                self._models[route] = model
            for entity in self._models[route](sentence).ents:
                start, end = entity.start_char, entity.end_char
                if not 0 <= start < end <= len(sentence):
                    raise ValueError("Invalid local recheck offsets.")
                label = labels.get(entity.label_)
                if label is None:
                    continue
                found.add((offset + start, offset + end, label))
                if (
                    routes == (0,)
                    and label == "ORG"
                    and _quoted_extension(sentence, start, end)
                ):
                    found.add((offset + start, offset + end + 1, label))
        return found

    def spans(
        self, text: str, supplemental: dict[Span, float] | None = None
    ) -> set[Span]:
        with self._lock:
            scored = self._gliner(text)
            kept = set(scored)
            review = {span for span, score in scored.items() if score <= RECHECK_CUTOFF}
            additions: set[Span] = set()
            if review:
                for offset, sentence in _sentences(text):
                    stop = offset + len(sentence)
                    spots = {p for p in review if offset <= p[0] and p[1] <= stop}
                    if not spots:
                        continue
                    routes = _route(sentence)
                    matched = self._recheck(sentence, offset, routes)
                    kept.difference_update(spots - matched)
                    if routes == (0,):
                        inside = {p for p in scored if offset <= p[0] and p[1] <= stop}
                        additions.update(
                            _org_additions(sentence, offset, inside, spots, matched)
                        )
            other = {p for p in kept if p[2] != "ORG"}
            accepted = kept | {
                p for p in additions if not any(_overlaps(p, item) for item in other)
            }

            if supplemental is not None:
                cyrillic, latin = _script_counts(text)
                for span in scored.keys() - accepted:
                    start, end, label = span
                    surface = text[start:end]
                    if sum(
                        word[0].isupper() for word in CAPITALIZED_WORDS.findall(surface)
                    ) >= 2 or (
                        cyrillic > latin
                        and label == "ORG"
                        and "«" in surface
                        and "»" in surface
                    ):
                        supplemental[span] = scored[span]
            return accepted


_ENGINE_LOCK = Lock()


@lru_cache(maxsize=1)
def _engine(model_dir: Path) -> _Engine:
    return _Engine(model_dir)


def model_spans_for(
    model_dir: Path, text: str, supplemental: dict[Span, float] | None = None
) -> set[Span]:
    """Run the pinned local NER pipeline, suppressing runtime details on failure."""
    try:
        with _ENGINE_LOCK:
            engine = _engine(model_dir)
        return engine.spans(text, supplemental)
    except Exception:
        raise ConfigurationError(
            "Enabled local NER could not process the input."
        ) from None
