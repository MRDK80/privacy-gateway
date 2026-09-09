"""Недоверенный ответ внешней модели и повреждённые токены.

Ответ внешнего обработчика — недоверенный ввод. Пример готовит один
синтетический запрос и прогоняет пять вариантов ответа: токен сохранён,
удалён, повторён, искажён и выдуман. Каждый вариант выполняется дважды —
в строгом режиме и в мягком, — поэтому различие режимов видно на одних и
тех же данных.

Классификация кандидатов и строгий отказ выполняются внутри ``restore``
до подстановки исходных значений: при отказе открытый текст не
возвращается вообще.

Границы публичного контракта, которые пример не нарушает:

- публичный ``RestoredPayload`` содержит только ``text``,
  ``correlation_id``, ``tokens_restored`` и ``tokens_missing``;
- перечня оставшихся кандидатов, счётчика дублей и предупреждений в
  публичном API нет, поэтому пример их не обещает;
- аномалия повтора известного токена фиксируется самим примером, который
  сам сконструировал ответ, а не полем библиотеки.

Ограничения текущего классификатора, показанные явно:

- запись без квадратных скобок (``PROJECT_1``) кандидатом не считается
  вовсе, поэтому она не «искажённый токен», а невидимая для ``restore``
  строка; соответствующий токен просто попадает в пропавшие;
- мягкий режим оставляет неизвестные и искажённые кандидаты в тексте и
  продолжает работу, поэтому он не является безопасным для
  автоматического конвейера.

Вывод содержит только безопасные поля: имя варианта, режим, класс исхода
и публичные счётчики. Исходный текст, значения, имена токенов, контекст,
manifest, route и пути не печатаются.

Ожидаемое поведение всех вариантов считается успешным результатом
демонстрации, поэтому процесс завершается кодом ``0``. Код ``1`` означает
нарушение инварианта, код ``2`` — запуск не из корня репозитория.

Запуск из корня репозитория:

    python examples/04_untrusted_response.py

Требуется активный ключ в системном keyring: ``pgw key create``.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from privacy_gateway import GatewayConfig, PrivacyGateway, StrictTokenError

ENTITIES_CONFIG = Path("config.example") / "entities.yaml"

# Только синтетические значения, разрешённые политикой репозитория.
SYNTHETIC_PROJECT = "Проект-Орион"
SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret

SYNTHETIC_REQUEST = (
    f"Задача по проекту {SYNTHETIC_PROJECT}.\n"
    f"Ответ направьте на {SYNTHETIC_EMAIL}\n"
)

SYNTHETIC_VALUES = (SYNTHETIC_PROJECT, SYNTHETIC_EMAIL)

VARIANTS = ("preserved", "removed", "duplicated", "malformed", "fabricated")
MODES = ("strict", "lenient")

# Грамматика токена продублирована здесь намеренно: пример строит
# недоверенный ответ из собственного защищённого текста и не имеет права
# зависеть от внутренних модулей. Классификацию по-прежнему выполняет
# ``restore``; см. docs/token-format.md.
_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9]*_[1-9][0-9]*\]")

# Ожидаемый публичный исход: ``restored`` либо имя класса ошибки.
EXPECTED_OUTCOME: dict[tuple[str, str], str] = {
    ("preserved", "strict"): "restored",
    ("removed", "strict"): "restored",
    ("duplicated", "strict"): "restored",
    ("malformed", "strict"): "StrictTokenError",
    ("fabricated", "strict"): "StrictTokenError",
    ("preserved", "lenient"): "restored",
    ("removed", "lenient"): "restored",
    ("duplicated", "lenient"): "restored",
    ("malformed", "lenient"): "restored",
    ("fabricated", "lenient"): "restored",
}

# Смещение счётчиков относительно варианта ``preserved``:
# (сдвиг tokens_restored, ожидаемый tokens_missing).
EXPECTED_DELTA: dict[str, tuple[int, int]] = {
    "preserved": (0, 0),
    "removed": (-1, 1),
    "duplicated": (0, 0),
    "malformed": (-1, 1),
    "fabricated": (0, 0),
}


@dataclass(frozen=True)
class VariantOutcome:
    """Безопасный итог одного варианта недоверенного ответа."""

    variant: str
    mode: str
    outcome: str
    tokens_restored: int | None
    tokens_missing: int | None
    fabricated_token_unresolved: bool
    workspace_clean: bool


class RecordingProvider:
    """Локальный детерминированный обработчик недоверенного ответа.

    Получает только защищённый текст, применяет к нему заданную мутацию и
    запоминает вход, чтобы граница доверия проверялась фактически.
    """

    def __init__(self) -> None:
        """Создать обработчик с пустой историей вызовов."""
        self.calls = 0
        self.received: list[str] = []

    def __call__(self, protected_text: str, mutate: Callable[[str], str]) -> str:
        """Вернуть недоверенный ответ, построенный из защищённого текста."""
        self.calls += 1
        self.received.append(protected_text)
        return mutate(protected_text)


def _ordered_tokens(text: str) -> list[str]:
    """Вернуть уникальные токены в порядке первого вхождения."""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group()
        if token not in tokens:
            tokens.append(token)
    return tokens


def _mutation_target(text: str, tokens: list[str]) -> str | None:
    """Вернуть токен, встречающийся ровно один раз, либо ``None``."""
    for token in tokens:
        if text.count(token) == 1:
            return token
    return None


def _malformed(token: str) -> str:
    """Вернуть искажённый кандидат: разделитель заменён на дефис."""
    type_part, number = token[1:-1].rsplit("_", 1)
    return f"[{type_part}-{number}]"


def _fabricated(tokens: list[str]) -> str:
    """Вернуть синтаксически корректный токен, которого нет в манифесте."""
    type_part = tokens[0][1:-1].rsplit("_", 1)[0]
    number = 90
    candidate = f"[{type_part}_{number}]"
    while candidate in tokens:
        number += 1
        candidate = f"[{type_part}_{number}]"
    return candidate


def _mutator(
    variant: str, target: str, fabricated: str
) -> Callable[[str], str]:
    """Вернуть детерминированную мутацию защищённого текста."""
    if variant == "preserved":
        return lambda text: text
    if variant == "removed":
        return lambda text: text.replace(target, "", 1)
    if variant == "duplicated":
        return lambda text: text.replace(target, f"{target} {target}", 1)
    if variant == "malformed":
        return lambda text: text.replace(target, _malformed(target), 1)
    return lambda text: f"{text}Уточнение по {fabricated}.\n"


def run_variant(
    variant: str,
    mode: str,
    provider: RecordingProvider,
    base_dir: Path,
) -> VariantOutcome:
    """Выполнить один вариант и вернуть безопасный итог.

    Контекст восстановления освобождается в ``finally`` независимо от
    исхода, поэтому очистка выполняется после каждого варианта.
    """
    gateway = PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=base_dir,
            strict=mode == "strict",
        )
    )
    prepared = gateway.prepare(
        SYNTHETIC_REQUEST, correlation_id=f"untrusted-{variant}-{mode}"
    )

    outcome = "no_mutation_target"
    restored: int | None = None
    missing: int | None = None
    unresolved = True

    try:
        tokens = _ordered_tokens(prepared.text)
        target = _mutation_target(prepared.text, tokens)
        if target is not None:
            fabricated = _fabricated(tokens)
            response = provider(
                prepared.text, _mutator(variant, target, fabricated)
            )
            try:
                payload = gateway.restore(response, context=prepared.context)
            except StrictTokenError as exc:
                outcome = type(exc).__name__
            else:
                outcome = "restored"
                restored = payload.tokens_restored
                missing = payload.tokens_missing
                if variant == "fabricated":
                    unresolved = payload.text.count(fabricated) == 1
    finally:
        gateway.discard(prepared.context)

    return VariantOutcome(
        variant=variant,
        mode=mode,
        outcome=outcome,
        tokens_restored=restored,
        tokens_missing=missing,
        fabricated_token_unresolved=unresolved,
        workspace_clean=not any(base_dir.iterdir()),
    )


def _matches_expectation(outcome: VariantOutcome, baseline: int | None) -> bool:
    """Проверить вариант против зафиксированного публичного контракта."""
    expected = EXPECTED_OUTCOME[(outcome.variant, outcome.mode)]
    if outcome.outcome != expected:
        return False
    if not outcome.fabricated_token_unresolved:
        return False
    if expected != "restored":
        return outcome.tokens_restored is None and outcome.tokens_missing is None
    if baseline is None:
        return False
    if outcome.tokens_restored is None or outcome.tokens_missing is None:
        return False
    delta, expected_missing = EXPECTED_DELTA[outcome.variant]
    if outcome.tokens_restored != baseline + delta:
        return False
    return outcome.tokens_missing == expected_missing


def _format(outcome: VariantOutcome, as_expected: bool) -> str:
    """Собрать безопасную строку отчёта об одном варианте."""
    restored = "n/a" if outcome.tokens_restored is None else outcome.tokens_restored
    missing = "n/a" if outcome.tokens_missing is None else outcome.tokens_missing
    return (
        f"variant={outcome.variant} mode={outcome.mode} "
        f"outcome={outcome.outcome} tokens_restored={restored} "
        f"tokens_missing={missing} "
        f"fabricated_token_unresolved={outcome.fabricated_token_unresolved} "
        f"workspace_clean={outcome.workspace_clean} "
        f"as_expected={as_expected}"
    )


def run_untrusted_response_demo(provider: RecordingProvider) -> int:
    """Выполнить все варианты, напечатать отчёт и вернуть код завершения."""
    if not ENTITIES_CONFIG.is_file():
        print("Запустите пример из корня репозитория privacy-gateway.")
        return 2

    base_dir = Path(tempfile.mkdtemp(prefix="pgw-untrusted-"))
    outcomes: list[VariantOutcome] = []
    baseline: int | None = None
    all_expected = True

    try:
        for mode in MODES:
            for variant in VARIANTS:
                outcome = run_variant(variant, mode, provider, base_dir)
                if baseline is None and outcome.tokens_restored is not None:
                    baseline = outcome.tokens_restored
                as_expected = _matches_expectation(outcome, baseline)
                all_expected = all_expected and as_expected
                outcomes.append(outcome)
                print(_format(outcome, as_expected))
    finally:
        if not any(base_dir.iterdir()):
            base_dir.rmdir()

    leak_free = all(
        value not in received
        for received in provider.received
        for value in SYNTHETIC_VALUES
    )
    workspace_clean = all(item.workspace_clean for item in outcomes)

    print(f"provider_calls={provider.calls}")
    print(f"protected_leak_free={leak_free}")
    print(f"workspace_clean={workspace_clean}")
    print(f"all_variants_expected={all_expected}")

    if all_expected and leak_free and workspace_clean:
        return 0
    return 1


def main() -> int:
    """Запустить пример с локальным записывающим обработчиком."""
    return run_untrusted_response_demo(RecordingProvider())


if __name__ == "__main__":
    raise SystemExit(main())
