"""Generate the versioned synthetic #446 train and development corpora."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from tools.ner_baseline import Span

# Each split owns its template families and name inventory. Dictionary
# probes appear only in the explicitly listed hand-authored edge cases.
TRAIN_ROOTS = {
    "en": (
        "Albriven",
        "Bessowick",
        "Cindernoll",
        "Draulmere",
        "Eldenspire",
        "Fennoward",
        "Glimberton",
        "Hollanreach",
        "Irongleam",
        "Jastervale",
        "Kethowen",
        "Lorrenshade",
        "Merrisnook",
        "Nobbishaw",
        "Ordelwick",
        "Perrilmore",
        "Quennifold",
        "Rostermint",
        "Sallowfern",
        "Tessengate",
    ),
    "ru": (
        "Альнов",
        "Бельтарин",
        "Вельдар",
        "Гарвен",
        "Дорвел",
        "Ермун",
        "Жармин",
        "Зертан",
        "Истрел",
        "Кельнар",
        "Ландрин",
        "Мелдар",
        "Нельвар",
        "Ольмер",
        "Пелтарин",
        "Ровдар",
        "Селвин",
        "Торвен",
        "Ульден",
        "Фарвел",
    ),
}
DEV_ROOTS = {
    "en": (
        "Yarrowspindle",
        "Zenniford",
        "Ullenspear",
        "Varrenquill",
        "Wistlemarch",
        "Xelliver",
    ),
    "ru": ("Харлон", "Царвел", "Чаррин", "Шермин", "Энвер", "Ярмун"),
}

TRAIN_FRAMES = {
    "en": (
        "Morning update,\n{person} mailed the roster from {place} to {org} for review.",
        "Routing request for {org}\nForward the barcode to {person} in {place}.",
        "Signing off,\n{person}\n{org} — {place}",
        "Ping {person}! Is {org} still hiring in {place}?",
        "Courier note: parcels should arrive in {ambiguous_place} by dawn.",
        "Courier note: parcels should be signed for by {ambiguous_org} by dawn.",
        "Ticket bounced. No contact was set for the draft.",
        "{other_person} ждёт письма.\nSend the reply from {place} via {org} today.",
        "Update: {person} called twice. {person} left no voicemail.",
    ),
    "ru": (
        "Доброе утро,\n{person} направил квитанцию из {place_gen} в «{org}».",
        "Заявка для «{org}»\nПросьба позвонить {person_dat} по прибытии в {place}.",
        "С уважением,\n{person}\n{org} — {place}",
        "Срочно: {person} уже в {place}? Свяжитесь с «{org}».",
        "Копия заявки сохранена. Адрес не задан.",
        "Очередь открыта: {person} получил код. {person} пришлёт его отдельно.",
        "Please notify {other_person}.\n"
        "Новый талон для «{org}» отправлен из {place_gen}.",
        "Передайте накладную в ООО «{org_core}» через {person_acc}.",
        "Пометка для отдела: {place} оставили в графе назначения.",
    ),
}
DEV_FRAMES = {
    "en": (
        "Hi team,\nCould the representative of {org} bring "
        "{person} the badge while in {place}?",
        "Access form\nApplicant: {person}\nIssuer: {org}\nSite: {place}",
        "{person}\nThanks again on behalf of {org}, {place}",
        "Still here in {place}. {person} says {org} may call later.",
        "The delegation toured {ambiguous_place} before lunch.",
        "The delegation hired {ambiguous_org} before lunch.",
        "The broken link needs replacing. Nothing else changed.",
        "Подтвердите встречу в {other_place}.\n"
        "{org} asked for an appointment near {place}.",
        "{person} booked it. A receipt for {person} should be on its way.",
    ),
    "ru": (
        "Здравствуйте,\nВстречу для «{org}» в {place_prep} назначил {person}.",
        "Новый пропуск\nВладелец: {person}\nПартнёр: {org}\nПункт: {place}",
        "{person}\nБлагодарю коллег из «{org}», {place}",
        "Не забудьте ответить {person_dat}. Из {place_gen} пишет «{org}».",
        "Черновик удалён. Отправка отменена.",
        "Пусть {person} проверит отчёт, а {person} подтвердит исходящий номер.",
        "Delivery to {other_place} is pending.\n"
        "Из {place_gen} вестей нет, ожидает «{org}».",
        "Прошу переоформить оплату на ООО «{org_core}» при встрече с {person_ins}.",
        "Отправление уйдёт утром, пункт на карте отмечен как {place}.",
    ),
}

TRAIN_EDGES = (
    ("en", "An index lists {name} as a code. No one signed it.", "Milo Wexfern", ""),
    (
        "en",
        "Dispatch assigned {name} to crate nine.",
        "Copperkite Guild Logistics",
        "ORG",
    ),
    ("ru", "Шифр {name} напечатан на обороте бланка.", "Выметный Тарн", ""),
    ("ru", "Куратор {name} сверил запись.", "Синтовый Мирн Альтер", "PERSON"),
)
DEV_EDGES = (
    ("en", "The test string is {name}, printed in gray.", "Elowen Duskfern", ""),
    ("en", "Reply to {name} before tomorrow.", "Violetspoke Workshop Services", "ORG"),
    ("ru", "Образец надписи: {name}.", "коллегию Янтарный Шов", ""),
    ("ru", "Просьба уведомить {name} о переносе.", "артель Лунный Винт Восток", "ORG"),
)

PLACEHOLDER = re.compile(r"(ООО «)?\{(\w+)\}(?(1)»)")


def name_values(language: str, root: str, split: str) -> dict[str, tuple[str, str]]:
    if language == "en":
        person_prefix = "Tavrik" if split == "train" else "Surven"
        return {
            "person": (f"{person_prefix} {root}", "PERSON"),
            "place": (f"{root} Vale", "LOC"),
            "org": (f"{root} Bureau Ltd", "ORG"),
            "ambiguous_place": (f"{root} Shade", "LOC"),
            "ambiguous_org": (f"{root} Shade", "ORG"),
        }
    person_prefix = "Варлент" if split == "train" else "Эрвинт"
    return {
        "person": (f"{person_prefix} {root}ов", "PERSON"),
        "person_dat": (f"{person_prefix}у {root}ову", "PERSON"),
        "person_acc": (f"{person_prefix}а {root}ова", "PERSON"),
        "person_ins": (f"{person_prefix}ом {root}овым", "PERSON"),
        "place": (f"{root}ск", "LOC"),
        "place_gen": (f"{root}ска", "LOC"),
        "place_prep": (f"{root}ске", "LOC"),
        "org": (f"{root}ская Лига", "ORG"),
        "org_core": (f"{root}ская Лига", "ORG"),
    }


def render(frame: str, values: dict[str, tuple[str, str]]) -> tuple[str, list[Span]]:
    chunks = []
    spans: list[Span] = []
    cursor = offset = 0
    for match in PLACEHOLDER.finditer(frame):
        literal = frame[cursor : match.start()]
        chunks.append(literal)
        offset += len(literal)
        text, label = values[match[2]]
        if match[1]:
            text = f"{match[1]}{text}»"
        chunks.append(text)
        if label:
            spans.append((offset, offset + len(text), label))
        offset += len(text)
        cursor = match.end()
    chunks.append(frame[cursor:])
    return "".join(chunks), spans


def documents(split: str) -> Iterator[dict[str, object]]:
    roots, frames, edges = (
        (TRAIN_ROOTS, TRAIN_FRAMES, TRAIN_EDGES)
        if split == "train"
        else (DEV_ROOTS, DEV_FRAMES, DEV_EDGES)
    )
    for language, language_frames in frames.items():
        for index, root in enumerate(roots[language], 1):
            other = "ru" if language == "en" else "en"
            values = name_values(language, root, split)
            counterparts = name_values(other, roots[other][index - 1], split)
            values.update(
                {f"other_{field}": counterparts[field] for field in ("person", "place")}
            )
            for family, frame in enumerate(language_frames, 1):
                mixed = family == (8 if language == "en" else 7)
                text, spans = render(frame, values)
                if not spans and index > 1:
                    continue
                yield {
                    "id": f"{split}-{language}-{family:02d}-{index:02d}",
                    "language": language,
                    "text": text,
                    "spans": spans,
                    "mixed": mixed,
                }
    for index, (language, frame, surface, label) in enumerate(edges, 1):
        text, spans = render(frame, {"name": (surface, label)})
        yield {
            "id": f"{split}-{language}-dictionary-{index}",
            "language": language,
            "text": text,
            "spans": spans,
            "mixed": False,
        }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for split in ("train", "dev"):
        with (args.out / f"{split}.jsonl").open(
            "x", encoding="utf-8", newline="\n"
        ) as output:
            for row in documents(split):
                output.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
