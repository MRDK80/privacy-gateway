# Contributing

Этот документ — канонический и обязательный Definition of Done для `privacy-gateway`. Он действует для людей и для автоматизированных исполнителей (coding agents). Краткая императивная версия для агентов — [`AGENTS.md`](AGENTS.md); операционный checklist — [`.github/pull_request_template.md`](.github/pull_request_template.md). При расхождении формулировок приоритет у этого файла.

## Окружение

### Python policy

Разделяются четыре разных понятия: совместимость установки, поддерживаемые minor-версии, exact CI matrix и фактическая версия локального gate.

- Нижняя граница совместимости установки объявлена в `pyproject.toml`: `project.requires-python = ">=3.11"`. Она разрешает установку на Python 3.11 и новее, но сама по себе не является обещанием поддержки каждой будущей minor-версии Python.
- Поддерживаемые minor-версии перечисляются Python classifiers в `pyproject.toml` и обязаны совпадать с версиями в exact CI matrix. В текущей конфигурации поддерживаются Python 3.11 и 3.12.
- Exact CI обязателен для каждой поддерживаемой minor-версии на Ubuntu latest и Windows latest. Текущая matrix: Python 3.11 и 3.12 на обеих ОС, плюс отдельный pre-commit workflow.
- Python 3.13 и новее не считаются поддерживаемыми, пока соответствующий classifier не добавлен в `pyproject.toml` и exact CI не подтверждён на всех поддерживаемых ОС. Отсутствие верхней границы в `requires-python` не заменяет это решение о поддержке.
- Локальный gate выполняется на любой версии, удовлетворяющей `requires-python`. Фактический вывод `python --version` обязательно приводится в PR и handover.
- Локальная minor-версия, отличающаяся от поддерживаемой CI matrix, допустима для локальной разработки, если она удовлетворяет `requires-python`, но её успешный gate не является доказательством официальной поддержки этой minor-версии и не заменяет exact CI.
- Если локальная версия не удовлетворяет `requires-python`, работа блокируется до смены окружения.
- Если `requires-python`, Python classifiers, CI matrix и документация расходятся, это блокирующее расхождение support policy. Оно исправляется в отдельной issue или в явно согласованном corrective scope, после чего повторяются полный local gate и exact CI.
- Используйте отдельное виртуальное окружение для репозитория.
- Проект устанавливается в editable-режиме вместе с dev-зависимостями ровно так, как они объявлены в `pyproject.toml`. Не выдумывайте команды и extras, которых нет в конфигурации проекта.

Фактическую нижнюю границу установки можно проверить непосредственно из конфигурации:

```bash
python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['requires-python'])"
```

Поддерживаемые minor-версии проверяются одновременно по Python classifiers в `pyproject.toml` и matrix в `.github/workflows/tests.yml`; эти списки должны совпадать.

### Linux (bash)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

Команда `python3.11` приведена как поддерживаемая версия и доступна не во всех дистрибутивах. Допустимо создать venv любым установленным интерпретатором, удовлетворяющим `>=3.11` (например `python3 -m venv .venv` или полный путь к интерпретатору); использованная версия фиксируется в отчёте. Такой локальный запуск не расширяет официальный список поддерживаемых minor-версий.

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

Если Python Launcher (`py`) отсутствует, используйте полный путь к интерпретатору, удовлетворяющему `requires-python`. Команды для `cmd.exe` здесь не приводятся, так как не проверялись.

### Источник версии пакета

Версия дистрибутива задаётся ровно в одном месте — литерал `__version__` в `src/privacy_gateway/__init__.py`. В `pyproject.toml` поле версии объявлено динамическим (`dynamic = ["version"]` и `[tool.setuptools.dynamic]` с `version = {attr = "privacy_gateway.__version__"}`) и при release не редактируется (ADR-81, #81).

- `__version__` обязан оставаться простым строковым литералом, совместимым с `ast.literal_eval`. Вычисления, вызовы функций, конкатенация и производные от импортов запрещены: setuptools читает атрибут статически из AST, а при неудаче попытается импортировать ещё не установленный пакет, и сборка упадёт.
- После изменения литерала обязателен повторный `python -m pip install -e ".[dev]"`. Metadata editable-установки не обновляется автоматически, поэтому до переустановки `tests/test_cli.py::test_version_matches_distribution_metadata` сравнит новый литерал с устаревшей metadata и упадёт.
- Тихий fallback на литерал при отсутствии distribution metadata не вводится: отсутствие метаданных не маскируется.
- Инварианты формы version policy проверяются `tests/test_package_version.py`; SPDX/license policy — `tests/test_package_license.py`.
- Фактические метаданные дистрибутива проверяет build gate exact CI (`tools/verify_package_build.py`, #85, #86): в каждой matrix cell из одноразовой копии `HEAD` собирается sdist, затем wheel из sdist, wheel устанавливается в чистое виртуальное окружение. Проверяются `METADATA` -> `Version`, `License-Expression: MIT`, `License-File: LICENSE`, отсутствие legacy `License` и license classifiers, расположение LICENSE в обоих архивах, отсутствие целевых deprecation warnings, `importlib.metadata.version("privacy-gateway")` и `privacy_gateway.__version__`. Ожидаемая версия берётся из AST литерала `__version__`, третий источник версии не вводится.
- Нижняя граница backend `setuptools>=77.0.3` проверяется отдельной boundary-сборкой на Ubuntu latest / Python 3.11: точный пин `77.0.3` в отдельном окружении и `python -m build --no-isolation`, с проверкой поля `Generator` в метаданных wheel. Обычная изолированная сборка нижнюю границу не доказывает, потому что ставит последнюю подходящую версию backend (ADR-86, #86).
- Артефакты сборки не создаются внутри рабочего дерева: `mypy` работает в режиме `strict` без `exclude`, а `detect-secrets` не исключает `build/` и `dist/`, поэтому загрязнение checkout ломало бы обязательные проверки.

### Фиксация фактической версии

После активации окружения зафиксируйте версию и приведите её в PR и handover:

```bash
python --version
```

## Модель ветвления

- `main` — защищённая релизная ветка.
- `roadmap/<roadmap-issue>-<slug>` — временная интеграционная ветка roadmap,
  созданная от актуального `main`.
- `<type>/<issue>-<slug>` — ветка отдельной задачи, созданная от
  соответствующей roadmap-ветки.

Разрешены только направления PR:

```text
<task-branch> -> roadmap/<roadmap-issue>-<slug>
roadmap/<roadmap-issue>-<slug> -> main
```

Task PR непосредственно в `main`, прямой push в `main` и прямой push в
существующую roadmap-ветку запрещены. Фактические head, base и SHA проверяются
через GitHub. Force push и удаление `main` или roadmap-ветки запрещены.

Если `main` изменился, сначала обновляется roadmap-ветка, затем task-ветки.
Task-ветка удаляется после merge. Roadmap-ветка удаляется после успешного
merge в `main` и post-merge CI.

Rulesets, созданные для приватного репозитория на тарифе без enforcement,
считаются подготовленными, но не действующими. После публикации репозитория
их фактическое применение проверяется отдельно.

## Начало работы

1. Определите roadmap issue и соответствующую roadmap-ветку.
2. Проверьте актуальность roadmap-ветки относительно `main`.
3. Зафиксируйте SHA `main`, roadmap-ветки и будущую base PR.
4. Создайте task-ветку от соответствующей roadmap-ветки.
5. Прочитайте task issue, roadmap issue, связанные ADR, `SECURITY.md` и
   архитектурные документы до изменения файлов.
6. Подтвердите causal scope и явно перечислите то, что в него не входит.
7. Если принадлежность задачи к roadmap неизвестна, остановитесь и запросите
   решение владельца.

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

После создания PR подтверждаются фактические GitHub check runs для каждой поддерживаемой комбинации:

- Ubuntu latest, Python 3.11;
- Ubuntu latest, Python 3.12;
- Windows latest, Python 3.11;
- Windows latest, Python 3.12;
- отдельный pre-commit workflow.

В каждой из четырёх matrix cells после `pytest`, `ruff check .`, `mypy .` и
`detect-secrets` выполняется build gate — шаг «Сборка дистрибутива и сверка
версии»; на Ubuntu latest / Python 3.11 дополнительно выполняется шаг «Нижняя
граница setuptools 77.0.3». Число обязательных check runs при этом не меняется и
остаётся равным пяти: packaging-проверки входят в существующие cells, а не
образуют отдельную matrix.

Падение build gate окрашивает тот же check run, что и падение тестов, поэтому
причину смотрят по имени упавшего шага в логе job.

При добавлении новой поддерживаемой Python minor-версии она одновременно добавляется в classifiers и exact CI matrix на обеих ОС. До успешного выполнения этих check runs поддержка новой версии не считается подтверждённой.

CI-результат подтверждается только первичными артефактами GitHub (check runs, run/job ID, conclusion). Он не выводится из факта merge, из текста исполнителя и из локальных результатов. Локальные результаты и CI указываются отдельными блоками.

## Отчётность в PR и handover

Обязательно указывается:

- base SHA, имя ветки, HEAD SHA (и merge SHA, если merge выполнен);
- фактический вывод `python --version` окружения, в котором выполнялся локальный gate;
- значение `project.requires-python` как нижняя граница совместимости установки;
- поддерживаемые minor-версии из classifiers и соответствующая exact CI matrix;
- локальная версия Python отдельно от всех exact CI versions;
- точная команда установки dev-зависимостей: `python -m pip install -e ".[dev]"`;
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

## Два уровня gate

Для task PR необходимы полный локальный gate текущего SHA, правильное
направление task-ветка → roadmap-ветка и успешный exact CI. После merge
получаются новый SHA roadmap-ветки и его post-merge CI; CI до merge не является
CI нового merge SHA.

Перед roadmap PR в `main` все задачи roadmap должны быть завершены, roadmap
синхронизирована с актуальным `main`, полный локальный gate повторён, итоговый
diff относительно `main` проверен, а exact CI PR успешен. После merge отдельно
проверяется post-merge CI нового SHA `main`. Успешные task CI не заменяют
итоговый roadmap CI.

## Статусы

- `TASK READY FOR REVIEW` — task-изменения применены, локальный gate зелёный,
  task PR направлен в правильную roadmap-ветку и exact CI текущего SHA успешен.
- `TASK READY FOR REVIEW WITH EXCEPTIONS` — task готов, но перечислены
  неподтверждённые ограничения.
- `TASK DONE` — task PR слит и post-merge CI нового SHA roadmap-ветки успешен.
- `ROADMAP READY FOR RELEASE` — все задачи имеют `TASK DONE`, roadmap
  синхронизирована с `main`, полный gate зелёный, PR в `main` создан и его CI
  успешен.
- `ROADMAP READY FOR RELEASE WITH EXCEPTIONS` — явно перечислены оставшиеся
  неподтверждённые ограничения.
- `ROADMAP DONE` — roadmap PR слит, новый SHA `main` подтверждён, post-merge CI
  успешен, а roadmap-ветка удалена либо объяснено её сохранение.
- `BLOCKED` — обязательная проверка падает или не выполнена, SHA/head/base
  неизвестны, направление PR неверно либо продолжение небезопасно.

Статус `DONE` без префикса `TASK` или `ROADMAP` запрещён. Merge сам по себе не
является доказательством review. Если отдельный GitHub Review отсутствует,
пишите: «GitHub Review отсутствует».
