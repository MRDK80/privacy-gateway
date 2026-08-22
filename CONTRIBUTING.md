# Contributing

Этот документ — канонический и обязательный Definition of Done для `privacy-gateway`. Он действует для людей и для автоматизированных исполнителей (coding agents). Краткая императивная версия для агентов — [`AGENTS.md`](AGENTS.md); операционный checklist — [`.github/pull_request_template.md`](.github/pull_request_template.md). При расхождении формулировок приоритет у этого файла.

## Окружение

- Поддерживаемая версия Python соответствует CI-матрице проекта: Python 3.11 на Ubuntu latest и Windows latest.
- Версия Python в локальном окружении не должна расходиться с `pyproject.toml` и с `.github/workflows/`. Обнаруженное расхождение фиксируется отдельной issue, а не «правится по пути».
- Используйте отдельное виртуальное окружение для репозитория.
- Устанавливайте проект в editable-режиме вместе с dev-зависимостями ровно так, как они объявлены в `pyproject.toml`. Не выдумывайте команды и extras, которых нет в конфигурации проекта.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# затем editable + dev-зависимости строго по pyproject.toml
pre-commit install
```

## Начало работы

1. Синхронизируйте `main` с origin.
2. Зафиксируйте фактический base SHA (`git rev-parse HEAD`) — он указывается в PR и handover.
3. Создайте отдельную ветку от актуального `main`.
4. Прочитайте issue, связанные ADR, `SECURITY.md` и архитектурные документы до изменения файлов.
5. Подтвердите scope (какие файлы входят и какие явно не входят) до первой правки.

## Scope discipline

- Одна причинная область — одна issue и один ограниченный PR.
- Несвязанный рефакторинг, переформатирование и «попутные улучшения» в PR не входят.
- Новые находки описываются отдельной issue; они не исправляются в текущем PR без явного согласования.
- Roadmap-, audit- и security-issues, tags, releases, visibility и настройки репозитория не меняются автоматически.
- Closing keyword (`Fixes #N`) применяется только после полного gate, подтверждённого CI и принятия результата владельцем.

## Security-sensitive изменения

- Threat model и контрактное решение фиксируются документально до production-кода.
- Для подтверждённого дефекта сначала добавляется воспроизводящий regression/failure-path тест, затем исправление.
- Реальные secrets, credentials, PII, внутренние хосты и пользовательские пути не попадают в код, тесты, fixtures, issues, PR и отчёты.
- Все fixtures и воспроизведения — синтетические и изолированные.
- `detect-secrets` из pre-commit обязателен; изменения `.secrets.baseline` объясняются в PR.

## Обязательный локальный quality gate

Для любого изменения code, tests, configuration **или** documentation перед статусом `READY FOR REVIEW` или `DONE` выполняются все четыре команды:

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

- Documentation-only diff **не** освобождает от полного gate.
- `git diff --check` — полезная дополнительная проверка, но она не заменяет ни одну из четырёх обязательных команд.
- Если любая из четырёх команд не выполнена или падает, используется `BLOCKED` либо `READY FOR REVIEW WITH EXCEPTIONS` с явным перечислением непройденного.

## Exact GitHub CI

После создания PR подтверждаются фактические GitHub check runs:

- Ubuntu latest, Python 3.11;
- Windows latest, Python 3.11;
- pre-commit workflow.

CI-результат подтверждается только первичными артефактами GitHub (check runs, run/job ID, conclusion). Он не выводится из факта merge, из текста исполнителя и из локальных результатов. Локальные результаты и CI указываются отдельными блоками.

## Отчётность в PR и handover

Обязательно указывается:

- base SHA, имя ветки, HEAD SHA (и merge SHA, если merge выполнен);
- точные выполненные команды локального gate;
- фактическое число passed/skipped тестов;
- фактическое число файлов, проверенных `mypy`;
- результат `ruff check .`;
- результат `pre-commit run --all-files`, включая `detect-secrets`;
- отдельный список GitHub check runs с conclusion и, по возможности, run/job ID;
- список изменённых файлов и causal scope;
- compatibility/breaking/release impact;
- остаточные риски и exceptions;
- статус review только по фактическому GitHub Review.

Формулировка «gate зелёный» без первичных результатов недопустима.

## Статусы

- `READY FOR REVIEW` — реализация и все четыре обязательные локальные проверки завершены, PR ожидает review/merge, состояние CI указано явно.
- `READY FOR REVIEW WITH EXCEPTIONS` — работа завершена, но одна или несколько обязательных проверок отсутствуют либо не подтверждены; они перечислены явно.
- `BLOCKED` — обязательная проверка падает либо безопасное продолжение требует решения владельца.
- `DONE` — изменение смержено, полный локальный gate зафиксирован первичными результатами и exact CI подтверждён.

Если хотя бы одна обязательная проверка не выполнена или не подтверждена, `DONE` запрещён.

Merge сам по себе не является доказательством review. Если отдельный GitHub Review отсутствует, это пишется прямо: «GitHub Review отсутствует».
