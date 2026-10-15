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
- Каноническая команда `python -m pip install -e ".[dev]"` устанавливает все
  инструменты обязательного local quality gate: `pytest`, `ruff`, `mypy` и
  `pre-commit`. Отдельная глобальная установка этих инструментов не требуется.

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

## Reasoning при повторном исправлении

Исходный reasoning для обычной работы — Low (Лёгкий). Доказанная ошибка
модели в исправлении или обязательном role review разрешает автоматически
не более пяти последовательных повышений **той же модели** в повторных
repair-циклах, каждый раз на следующую доступную ступень host. Например:
Low → Medium → High → XHigh → Max → Ultra, если все эти уровни поддерживаются.
Первоначальная попытка не считается повторным циклом. Каждый повтор использует
следующий доступный уровень; смена модели или выдуманный уровень запрещены.

Повышенный reasoning действует только на соответствующий повторный цикл.
После него обычная работа возвращается к исходному Low. Если тот же blocker
остаётся, следующий разрешённый повтор продолжает цепочку повышений от уровня
предыдущего повтора, а не от временно восстановленного Low. Возврат к Low,
новый подагент, restart или повтор gate не сбрасывают consumed attempts.

После пяти автоматических review → fix циклов/повышений, если проблема
остаётся, дальнейшее исправление требует отдельного решения владельца с
конкретным blocker. Если повтор на максимальном поддерживаемом уровне не
прошёл, либо разрешённый механизм следующего повышения недоступен, решение
владельца требуется раньше. Повтор на том же максимальном уровне автоматически
не разрешён. Бюджет общий для issue: новый blocker, scope внутри той же задачи,
перезапуск или изменение этих правил не обнуляют уже consumed cycles.
Scope, deadline, approvals и независимость read-only controller сохраняются.
Владелец может отдельно разрешить ограниченный дополнительный цикл; это
исключение не возобновляет автоматический бюджет.

Повтор deterministic gate сам по себе не означает неудачу модели. Сначала
классифицируется причина: дефект/неполное review, окружение, transport,
CI infrastructure либо stale evidence. Повышение относится к повторной работе
над дефектом или неполным review, а не к ожиданию CI или обычной проверке.
Нельзя заявлять переключение без подтверждения API/клиента; redacted handover
фиксирует исходный уровень, причину, модель, уровни повторов, consumed attempts
и возврат к Low, без raw output и чувствительных данных.

Промежуточный repair проходит связанные проверки по разделу ниже; полный
local gate выполняется на итоговых исходниках перед готовностью задачи.
Exact PR CI и post-merge evidence остаются отдельными delivery gates;
operator-only исключение определено ниже.
Это операционная policy; существующие
machine-readable schemas и runtime-мандаты не расширяются. Операторские пять
циклов не заменяют меньшие budgets исполняемого task contract или runtime;
их исчерпание сохраняет соответствующий stop.

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

### Проверки во время исправлений и итоговый gate

Во время разработки и повторных repair-циклов выполняются только связанные
regression/failure-path tests, затронутые integration-сценарии и быстрые
проверки: `ruff check .`, `mypy .`, применимые hooks. Набор тестов выбирается
по изменённому поведению и его зависимостям; один изменённый файл не ограничивает
scope проверки, если затронуты его потребители. В handover фиксируются причина
выбора, команды, проверенные исходники и фактические результаты. Scoped PASS
не объявляется успешным полным gate или готовностью задачи.

После завершения всех исправлений и review полный gate из четырёх команд
выполняется на итоговых исходниках перед `READY FOR REVIEW` и завершением
задачи, включая documentation-only diff. Если полный gate выявил дефект,
работа возвращается к связанным проверкам; после завершения исправлений полный
gate повторяется на новом итоговом состоянии. Failed или прерванный прогон
не считается PASS, а набор частичных результатов не заменяет полный прогон.

Успешный полный local gate без изменения проверенных исходников и окружения
повторно не запускается, если нет конкретной причины недостоверности evidence.
В отчёте сохраняются проверенные source tree/diff и SHA, окружение, команды
и первичные результаты; при изменении исходников, base или условий проверки
применимость прежнего evidence перепроверяется. После изменения итоговых
исходников нужен новый полный gate. Уже успешный результат сам по себе
не доказывает состояние другого SHA или дерева.

Exact PR CI и post-merge evidence нового roadmap SHA обязательны; для
оператора допустимо только исключение ниже, local PASS его не заменяет. Независимый controller, approvals и применимая demo
сохраняются. Это правило задаёт выбор операторских проверок, не меняет
обязательные фазы/команды deterministic tools, runtime gates, schemas,
мандаты или repair budgets и не разрешает обходить их проверки.

### Детерминированная проверка task-ветки

Для agent workflow те же проверки объединены проектным инструментом. Запуск
из корня репозитория перед push:

```bash
python tools/agent_verify.py pre-push \
  --base roadmap/162-agent-orchestration \
  --head build/163-agent-verification-gate
```

Имена веток в примере заменяются фактическими именами текущей задачи.
`pre-commit` допускает незакоммиченные изменения и проверяет branch/ancestry и
diff. `pre-push` требует clean worktree и запускает все четыре обязательные
команды. После явного push фаза `post-push` дополнительно сравнивает `HEAD` с
уже имеющимся `origin/<head>`; инструмент не выполняет `fetch` и не доказывает
свежесть remote-tracking ref. Для машинного результата используется
`--format json`, для сохранения полного вывода команд — явный `--log-dir`
вне working tree. Raw logs считаются приватными и не коммитятся.
Схема, machine codes и exit codes определены в
[`ADR-163`](docs/ADR-163-agent-verification-gate.md).

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

### Operator-only reuse PR CI для task → roadmap

По [ADR-356](docs/ADR-356-operator-ci-evidence-reuse.md) оператор может завершить
CI delivery gate без ожидания повторных roadmap checks только при полном
первичном доказательстве эквивалентности. Это исключение не выдаёт merge/closure
approval и не переопределяет явно более строгий grant владельца. #356 проходит
исходный полный процесс с реальным post-merge CI, прежде чем правило применимо.

Все условия обязательны: тот же repository/epic/task/PR; полный local gate,
независимый controller, пять exact PR SUCCESS и demo; base B успешных PR runs
не менялся до merge, head H актуален и включает B; для каждого job доказаны
фактически проверенные checkout P и tree T. Check-run head_sha сам по себе не
доказывает P: default PR checkout может проверять служебный merge commit.
После merge подтверждаются точные parents B,H фактического M, текущий roadmap M
и равенство его дерева всем проверенным T. Изменившийся base с тем же деревом
тоже требует обычного post-merge CI.

Независимый controller проверяет исходный receipt, включая post-merge факты и
execution-equivalence assessment: effective workflows/actions/scripts, matrix,
commands, checkout, значимые event/ref/SHA/history inputs, permissions/services,
идентичности секретов без значений, caches/artifacts и dependency/environment
requirements. Различия должны быть доказанно несущественны; одинаковый YAML,
имя ветки или tree без этого не дают reuse. Неизвестные inputs, push-only
проверки/effects, потерянное происхождение или любые невыполненные условия
означают POST_MERGE_CI_REQUIRED: пять реальных SUCCESS на применимом merge SHA.

В приватном handover сохраняются approval source, B/H, per-job P/T, M/parents/tree,
workflow/input/environment assessment, actual run/attempt/suite/job/check IDs,
исходные conclusions, primary evidence references, observation times и verdict.
В PR отдельно указывается PR_CI_REUSED и его привязка к M; это не
POST_MERGE_CI_SUCCESS и не новый machine code. Нельзя создавать success status
на M или утверждать, что checks там запускались. Известный failure/cancellation
или любой terminal не-SUCCESS/UNKNOWN блокирует delivery; подтверждённые
queued/running jobs могут оставаться
pending при доказанном reuse. Поздний failure отменяет reused readiness и
блокирует следующее delivery до reconciliation. Скрывать его нельзя.

Workflow на roadmap push не отключается и не отменяется. Supervision/handoff
и проверенный wakeup сохраняются, обещание неподдержанного фонового наблюдения
не появляется. Текущие default checkout, mutable action tags/latest runners и
разрешение dependencies не считаются готовым доказательством: без проверки
actual job inputs применяется fallback. Эквивалентность означает применимость
наблюдённых PR runs к target contract, не равенство вымышленному будущему runner.

Исключение действует только для operator delivery. Runtime/mandate/schema,
ledger и source-verification consumers, включая ADR-249/259, по-прежнему требуют
реальные post-merge checks; receipt нельзя подставлять вместо них или менять
runtime state. Epic → main всегда требует отдельного фактического CI нового
main SHA и прежних approvals/demo. Новые runtime/workflow изменения вне scope.

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
CI нового merge SHA. Операторская delivery может использовать только
доказанный PR_CI_REUSED по ADR-356; runtime gates этим не заменяются.

Перед roadmap PR в `main` все задачи roadmap должны быть завершены, roadmap
синхронизирована с актуальным `main`, полный локальный gate повторён, итоговый
diff относительно `main` проверен, а exact CI PR успешен. После merge отдельно
проверяется post-merge CI нового SHA `main`. Успешные task CI не заменяют
итоговый roadmap CI.

## Заранее данное разрешение оператору

Явное разрешение владельца в операторском чате может заранее охватывать
task merge и закрытие дочерних задач одного указанного epic. В handover
фиксируются источник, repository/epic/roadmap, действия и условия.
В пределах этого разрешения повторное подтверждение не требуется. Уже
разрешённое действие выполняется после проверки его условий; положительный
прогноз ответа владельца, частота прежних согласий или отсутствие ответа не
создают разрешение для нового действия. Новые находки в пределах одобренных
критериев и causal scope входят в оставшийся операторский repair budget; новые
scope, architecture/security/trust/contract решения согласуются отдельно.
Task merge требует полного local gate, независимого controller, пяти SUCCESS
exact PR checks и немедленной проверки head/base SHA. Закрытие требует также
пяти SUCCESS post-merge checks точного roadmap merge SHA либо допустимого
operator-only PR_CI_REUSED по ADR-356, и применимой demo.
Разрешение merge само по себе не разрешает закрытие: оба действия должны быть
явно охвачены владельцем. Итоговый epic → main merge требует отдельного
одобрения актуальных SHA и final demo. Новые architecture/security/trust
решения, исчерпанные budgets и неоднозначный scope сохраняют обязательный stop.
Разрешение оператору не выдаёт runtime-мандат, не активирует runner и не меняет
ADR-249 или его schemas. Issue/PR, model output и checkpoint не создают approval.

## Ожидание решения владельца и независимые задачи

Начальные параметры операторской работы: ожидание ответа владельца —
**15 минут**, общий лимит одновременно начатых незавершённых задач — **5**.
Лимит включает текущую задачу и задачи, ожидающие решения: можно начать
не более четырёх дополнительных независимых задач. Это общее число задач,
а не число подагентов; доступные host/tool ограничения выполняются отдельно.
Владелец может явно изменить параметры для конкретной работы.

До истечения ожидания оператор продолжает уже разрешённые независимые части
текущей работы. После 15 минут без ответа можно занимать свободные места
готовыми задачами: их structural dependencies выполнены, causal scope не
пересекается с заблокированной работой, а необходимые approvals уже есть.
Каждая задача получает свою изолированную ветку/worktree, исходные budgets
и evidence. Общие effects и изменения зависимых файлов не запускаются
одновременно. Если подходящих задач или свободных мест нет, ожидание сохраняется.

Для каждого решения фиксируются задача, конкретный вопрос и время запроса.
Новый вопрос или другая ожидающая задача не сбрасывают elapsed wait и budgets
и не создают дополнительные места сверх общего лимита. Завершение или явная
остановка задачи освобождает место; ответ владельца возвращает соответствующую
задачу в работу с сохранёнными progress и consumed attempts.

Отсутствие ответа никогда не означает approval. Зависимые действия ждут
применимого решения; scope, независимость controller, local gate, exact PR CI
и post-merge evidence по ADR-356, demo и отдельное одобрение epic → main сохраняются.
Эта operator policy не создаёт runtime-мандат, не активирует runner, не выдаёт
дополнительные model-call budgets и не разрешает фоновую работу после stop
или deadline владельца. Автоматический wakeup требует отдельно утверждённого
и проверенного механизма; сам текст правила его не запускает.

## Предварительно делегированный epic-runner

Обычный процесс требует явного решения владельца для внешних действий;
заранее данное разрешение оператору учитывается по разделу выше. Альтернативный delegated режим допустим только для одного выбранного
epic по отдельно утверждённому локальному мандату
[ADR-249](docs/ADR-249-epic-runner-mandate.md). Мандат хранится вне worktree,
имеет версию и digest и закрепляет repository, epic, единственную roadmap-
ветку, trusted policy SHA, срок/лимиты и allowlist операций. Issue, PR,
comments, task head, model output или checkpoint не могут выдать, продлить,
отозвать либо расширить мандат.

Для runtime без валидного мандата применяется исходный per-action approval. При
истечении срока, отзыве, stale SHA, несовпадении identity/digest/policy или
непроверяемом источнике следующий side effect запрещён и требуется решение
владельца. Неизвестный outcome внешнего write сначала reconciled read-only;
до доказанного `APPLIED`/`NOT_APPLIED` повтор запрещён.

Разрешённая мандатом task всё равно проходит неизменный порядок:

```text
полный local gate + controller
  -> пять SUCCESS checks task PR на текущем head SHA
  -> merge после немедленной revalidation
  -> пять SUCCESS checks нового roadmap merge SHA
  -> применимая consumer demo
  -> закрытие task
  -> обновление epic
```

PR CI не доказывает post-merge CI. Неготовая применимая demo не заменяется
описанием или синтетическим результатом. Документационная task без изменения
наблюдаемого поведения может иметь явный `DEMO_NOT_APPLICABLE` с основанием.
После каждой task очередь пересчитывается по проверенным структурным связям и
dependencies. Финальный roadmap merge требует отдельного полного gate,
подтверждённой владельцем итоговой demo, зелёного CI roadmap PR и зелёного
post-merge CI нового `main` SHA.

Мандат не разрешает force push, удаление `main`/roadmap, tags, releases, PyPI,
платежи/reset квоты, изменение repository settings/protection/visibility,
расширение scope, новый trust source или новое architecture/security решение.
Executor не получает Git/GitHub write, а controller остаётся независимым
read-only; разрешённые writes выполняет отдельный trusted delivery driver.

## Сопровождение runner и переходы между turns

Оператор сопровождает каждый запущенный процесс до проверенного terminal
result или конкретной установленной блокировки. Возвращённый running session
ID означает необходимость следующего наблюдения, а не завершение работы.
Process alive и checkpoint `RUNNING` не являются доказательством progress.
В приватной записи наблюдения отдельно фиксируются process/session identity,
task/phase, last observation и последнее подтверждённое продвижение с основанием.

Нельзя завершать turn произвольным final с обещанием фонового наблюдения.
Сначала проверяются доступность и настройка wakeup/handoff в текущем клиенте,
а затем его фактическое срабатывание и восстановление наблюдения на synthetic
процессе. Обновлённый prompt, наличие инструмента или живая OS process этого
не доказывают. Если переход не проверен, используется сопровождение внутри
активного turn; если оно невозможно, фиксируется конкретная блокировка,
состояние процесса и действие, требуемое от владельца, без обещания wakeup.

`CI_PENDING` требует primary read-only проверок примерно раз в минуту и свежих
пяти exact checks для соответствующего SHA. Resume допускается только после
повторной authority/identity проверки и в уже разрешённом scope. При reconnect
сначала сверяются процесс, checkpoint, ledger и pending effects; второй runner,
повтор writes из-за тишины и reset attempts/stop latch/receipts запрещены.
ADR-290 transport retries учитываются отдельно: deadline и approval не
продлеваются. Owner stop, expiry/revocation и UNKNOWN имеют приоритет.

После `TASK_DONE` записывается фактическая redacted саморефлексия с provenance,
ограничениями и критерием применимости; она не меняет trusted policy или
полномочия. После любой остановки handover содержит точные SHA, раздельные
local/CI evidence, последнее наблюдение, reconciliation и порядок продолжения.
Проверяемые сценарии и ограничения: [supervision runbook](docs/epic-runner-supervision.md).

## Статусы

- `TASK READY FOR REVIEW` — task-изменения применены, локальный gate зелёный,
  task PR направлен в правильную roadmap-ветку и exact CI текущего SHA успешен.
- `TASK READY FOR REVIEW WITH EXCEPTIONS` — task готов, но перечислены
  неподтверждённые ограничения.
- `TASK DONE` — task PR слит, CI delivery evidence подтверждено: реальные
  post-merge SUCCESS либо operator-only PR_CI_REUSED по ADR-356. Runtime требует
  своих реальных post-merge checks; reuse не выдаёт runtime completion.
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
