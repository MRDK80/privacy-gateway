# AGENTS.md

Обязательные инструкции для автоматизированных исполнителей (coding agents)
в репозитории `privacy-gateway`. Файл tool-agnostic: он не описывает настройки
конкретного инструмента. Он действует для всего дерева репозитория; вложенных
`AGENTS.md` сейчас нет. Полный источник правил процесса —
[`CONTRIBUTING.md`](CONTRIBUTING.md); этот файл не заменяет и не переопределяет
его.

## Назначение и trust boundary

- Privacy Gateway — local-first инструмент: обнаружение и псевдонимизация чувствительных данных выполняются на локальной машине, до передачи текста внешней языковой модели.
- Восстановление исходных значений тоже локальное: соответствие токенов и значений хранится в зашифрованном манифесте, ключ Fernet — в системном keyring.
- Trust boundary проходит между локальным приложением и внешней LLM. Наружу передаётся только защищённый текст, предназначенный для внешнего потребителя, а не весь `PreparedPayload`.
- Псевдонимизация не является анонимизацией: токены обратимы при доступе к локальному манифесту и ключу.
- Восстановленный ответ снова считается чувствительными данными.
- Канонические источники: [`README.md`](README.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/threat-model.md`](docs/threat-model.md). Не копируй их сюда.

## Публичные контракты

Публичный Library API — имена, экспортируемые пакетом `privacy_gateway`: `PrivacyGateway`, `GatewayConfig`, `PreparedPayload`, `RestoreContext`, `RestoredPayload`, `__version__` и исключения `PrivacyGatewayError`, `ConfigurationError`, `DetectionError`, `IntegrityError`, `KeyStoreError`, `RestoreError`, `StrictTokenError`. Reference: [`docs/LIBRARY_API.md`](docs/LIBRARY_API.md).

Публичный CLI — консольный скрипт `pgw`, объявленный как `privacy_gateway.cli:main`.
JSON-контракт operational commands задан в [`docs/ADR-150-cli-json-contract.md`](docs/ADR-150-cli-json-contract.md); не дублируй его envelope и machine codes.

Машиночитаемая интроспекция CLI выполняется командой `pgw --describe`: она
печатает каталог команд, параметров, форматов вывода, process exit codes и
machine codes. Контракт каталога задан в
[`docs/ADR-151-cli-introspection.md`](docs/ADR-151-cli-introspection.md).
Каталог формируется из фактического parser, поэтому не копируй его содержимое
в документацию и не поддерживай параллельный список команд вручную.

Остальные модули — internal implementation details: `pipeline`, `detector`, `tokenizer`, `manifest`, `restore`, `routing`, `crypto`, `keystore`, `validator`, `context_trust`, `publish`, `input_parser`, `models`. Не документируй их как публичный контракт и не считай стабильными.

`keystore.delete_key()` — library-only low-level Python API внутреннего модуля,
не экспортируемый публичным facade. Он не является CLI-командой. Подкоманды
`delete` в группе `pgw key` нет: группа содержит только `create`, `status` и
`rotate`.

## Структура репозитория

```text
src/privacy_gateway/   реализация пакета
tests/                 pytest-набор, включая contract и characterization тесты
docs/                  архитектура, безопасность, ADR, API, threat model
examples/              исполняемые примеры и их канонический индекс
tools/                 служебные проверки, вызываемые из CI
config.example/        примеры конфигурации детектора и маршрутизации
```

Канонические документы:

- [`README.md`](README.md) — обзор, установка, быстрый старт;
- [`SECURITY.md`](SECURITY.md) — политика работы с чувствительными данными;
- [`docs/SECURITY.md`](docs/SECURITY.md) — границы доверия, гарантии и их ограничения;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — конвейер, форматы артефактов, коды возврата;
- [`docs/LIBRARY_API.md`](docs/LIBRARY_API.md) — библиотечный API;
- [`docs/threat-model.md`](docs/threat-model.md), [`docs/token-format.md`](docs/token-format.md), [`docs/detection.md`](docs/detection.md) — модель угроз, формат токенов, детектирование;
- [`docs/DECISIONS.md`](docs/DECISIONS.md) и файлы `docs/ADR-*.md` — принятые решения;
- [`examples/README.md`](examples/README.md) — канонический индекс примеров.

## Development setup

Метаданные пакета разрешают установку на Python 3.11 и новее
(`requires-python = ">=3.11"`), но это не обещание поддержки каждой будущей
minor-версии. Официально поддерживаются Python 3.11 и 3.12: они перечислены в
classifiers и проверяются exact CI на Ubuntu и Windows. Другие minor-версии
этим CI не подтверждены; каноническая политика приведена в
[`CONTRIBUTING.md`](CONTRIBUTING.md).

Настройки pytest, Ruff и mypy находятся в
[`pyproject.toml`](pyproject.toml), а настройки pre-commit — в
[`.pre-commit-config.yaml`](.pre-commit-config.yaml). Отдельных `tox.ini`,
`pytest.ini`, `setup.cfg` и `mypy.ini` в репозитории нет.

Linux- и Windows-команды создания и активации окружения бери из
[`CONTRIBUTING.md`](CONTRIBUTING.md). Установка проекта и объявленных
репозиторием dev-зависимостей выполняется командой:

```bash
python -m pip install -e ".[dev]"
```

Перед gate проверь доступность команды `pre-commit`, затем выполни
`pre-commit install`. Сейчас `pre-commit` не объявлен в extra `dev`; его наличие
в текущем окружении не доказывает обратного. Если команда недоступна, верни
`BLOCKED`: не изменяй dependency metadata без отдельной issue.

## Локальный quality gate

Выполняй полный gate даже для documentation-only diff, из корня репозитория:

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

`git diff --check` — полезная дополнительная проверка, но она не заменяет ни
одну из четырёх обязательных команд.


Действующие настройки: `pytest` собирает только `tests`; `ruff` работает с `src` и `tests`, `line-length = 88`, правила `E`, `F`, `I`, `UP`; `mypy` запускается в режиме `strict` с `python_version = "3.11"`. Хуки [`.pre-commit-config.yaml`](.pre-commit-config.yaml): `detect-secrets` с `.secrets.baseline`, `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`.

Приводи первичные результаты: точную команду, рабочий каталог, exit code,
passed/failed/skipped/xfailed/xpassed для pytest, число проверенных файлов для
mypy, вывод Ruff и итог каждого pre-commit hook. Отсутствующий инструмент,
незапущенная команда или ненулевой exit code не являются успешной проверкой.

В PR и handover также указывай base SHA, имя ветки, HEAD SHA и merge SHA при
наличии; фактический `python --version`; значение `project.requires-python`;
поддерживаемые minor-версии из classifiers и соответствующую exact CI matrix;
точную команду установки `python -m pip install -e ".[dev]"`. Полный список
требований к отчётности находится в [`CONTRIBUTING.md`](CONTRIBUTING.md).

## GitHub Actions

Локальные результаты и GitHub check runs фиксируются отдельно: локальный gate не подтверждает CI, а CI не заменяет локальный gate.

- `CI` ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)) —
  матрица `ubuntu-latest` и `windows-latest` на Python 3.11 и 3.12; шаги
  `pytest`, `ruff check .`, `mypy .`, проверка secret-drift и сборка
  дистрибутива со сверкой версии.
- `pre-commit` ([`.github/workflows/pre-commit.yml`](.github/workflows/pre-commit.yml)) — Ubuntu, Python 3.11.
- `Main source guard`
  ([`.github/workflows/main-source-guard.yml`](.github/workflows/main-source-guard.yml)) —
  PR в `main` допускаются только из того же репозитория и только из ветки
  `roadmap/*`.

Packaging-проверки являются шагами внутри четырёх matrix cells, а не
отдельными check runs. Всего обязательных check runs пять: четыре matrix cells
и отдельный pre-commit workflow.

CI подтверждай только для текущего SHA, указывая conclusion и run/job ID.
`queued`, `pending`, `in_progress`, `skipped`, `cancelled`, `timed_out`,
`action_required` и отсутствующий check не являются успехом. После task merge
проверяй post-merge CI нового SHA roadmap-ветки; после roadmap merge —
post-merge CI нового SHA `main`.

## CLI

Список команд берётся из фактического parser, а не из документации по памяти:

```text
pgw detect ФАЙЛ    [--encoding ENC] [--config ENTITIES_CONFIG]
pgw prepare ФАЙЛ   [--out КАТАЛОГ] [--routing ROUTING_YAML]
                   [--config ENTITIES_CONFIG] [--encoding ENC] [--overwrite]
pgw restore ФАЙЛ   --route ROUTE_JSON [--out ФАЙЛ] [--overwrite]
                   [--manifest MANIFEST_JSON] [--lenient]
pgw key create     [--force]
pgw key status
pgw key rotate
```

`prepare` пишет `prompt.txt`, `route.json` и `manifest.json`. `restore` по
умолчанию строгий; `--lenient` переводит неизвестные и искажённые токены в
предупреждения (ADR-16). Путь `manifest.json` разрешается относительно каталога
`route.json`, если не задан явно (ADR-15). Позиционный аргумент `-` означает
stdin.

Машиночитаемый вид того же контракта:

```bash
pgw --describe
```

Вызов печатает один JSON object и не имеет побочных эффектов: он не читает
пользовательские файлы, `route.json` и `manifest.json`, не обращается к
keyring и не выполняет операционные команды. Вывод детерминирован при
неизменной версии кода. Лишние аргументы после `--describe` дают код `3`.
`--describe` является pre-parser control, поэтому существующий
человекочитаемый help не изменяется.

Не добавляй, не переименовывай и не удаляй команды, подкоманды и опции без отдельного решения, ADR и contract tests.

## Коды возврата

Канонический реестр — раздел «Коды возврата restore» в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Действующие значения:

| Результат | Код |
|---|---:|
| Успех, включая `--help` | 0 |
| Непредвиденная ошибка | 1 |
| PENDING | 2 |
| Ошибка входа, конфигурации, целостности или использования CLI | 3 |
| Ошибка keystore или отсутствие ключа | 4 |
| Строгий отказ по токенам (`RestoreStrictError`) | 5 |

Usage error argparse транслируется в код 3 на границе разбора argv (ADR-29), поэтому стандартный код 2 не пересекается с PENDING. Коды закреплены characterization и contract тестами; изменять их без отдельного решения, ADR и обновления тестов запрещено.

## stdout и stderr

- Данные идут в stdout либо в файл, указанный `--out`; диагностика идёт в stderr.
- Точные человекочитаемые строки являются частью контракта, включая prefix `Ошибка записи: ...`. Не переформулируй их попутно.
- При строгом отказе `restore` stdout остаётся пустым.
- Ни stdout, ни stderr, ни логи не должны содержать ключевой материал, исходные чувствительные значения или восстановленный текст в составе сообщений об ошибках.

## Security invariants

Формулировки сверяй с [`SECURITY.md`](SECURITY.md), [`docs/SECURITY.md`](docs/SECURITY.md) и действующими ADR. Не усиливай гарантии по сравнению с источником истины.

- Ключ никогда не логируется и не попадает в `repr`/`str`.
- Исходный текст и восстановленные значения не включаются в сообщения об ошибках и логи.
- Наружу передаётся только защищённый текст, предназначенный для внешнего потребителя.
- Восстановленный ответ снова считается чувствительным.
- Псевдонимизация не является анонимизацией.
- Атомарная видимость публикации не равна crash durability; формулировки «crash durable» и «набор артефактов публикуется транзакционно» неприменимы.
- Общая транзакция всех артефактов `prepare` не обещается.
- Cleanup временных файлов best-effort: если ОС отклоняет саму операцию `unlink`, временный файл с plaintext может остаться.
- Ротация ключа не является транзакцией keyring; общая atomic transaction guarantee backend отсутствует.
- Компрометация локального процесса, ОС-учётной записи или прямой доступ к raw keyring backend находятся вне базовой модели доверия.
- Не используй реальные secrets, credentials, PII и пользовательские пути; fixtures только синтетические.

## Минимальный causal diff

- Одна issue — один ограниченный PR. Не расширяй scope и не делай попутный рефакторинг.
- Сначала прочитай roadmap issue, task issue, связанные ADR и документы репозитория, затем меняй файлы.
- Security- и контрактные решения фиксируй документально до production-кода.
- Для подтверждённого дефекта сначала добавь воспроизводящий regression/failure-path тест, затем исправление.
- В documentation-задаче не меняй runtime-поведение, публичный API, коды возврата, версию, CHANGELOG, workflows и dependency metadata.

## Git workflow

```text
main
  <- roadmap/<roadmap-issue>-<slug>
       <- <type>/<task-issue>-<slug>
```

`main` — защищённая релизная ветка. Roadmap-ветка создаётся от актуального `main`, task-ветка — от соответствующей roadmap-ветки. Разрешённые направления pull request:

```text
<task-branch> -> roadmap/<roadmap-issue>-<slug>
roadmap/<roadmap-issue>-<slug> -> main
```

Запрещено:

```text
<task-branch> -> main                    # запрещено
push -> main                             # запрещено
push -> roadmap/<roadmap-issue>-<slug>   # запрещено
```

Также запрещено создавать task-ветку от `main` вместо roadmap-ветки. Если
`main` изменился, сначала обнови roadmap-ветку, затем task-ветки; нельзя
подмешивать новый `main` только в task-ветку. Проверяй фактические head, base и
SHA через GitHub, а не по именам веток. Force push к `main` и roadmap-ветке, а
также их удаление до завершения установленного процесса запрещены.

## Запрещённые автоматические действия

- merge pull request без подтверждения владельца;
- закрытие issues, включая roadmap- и audit-issues, без подтверждения;
- создание и перемещение tags, редактирование historical releases;
- force push;
- удаление `main` или roadmap-ветки;
- изменение branch protection, visibility и настроек репозитория;
- утверждение успешного CI без GitHub evidence;
- утверждение зелёного локального gate без фактического вывода команд.

## Статусы

Используй только `TASK READY FOR REVIEW`,
`TASK READY FOR REVIEW WITH EXCEPTIONS`, `TASK DONE`,
`ROADMAP READY FOR RELEASE`, `ROADMAP READY FOR RELEASE WITH EXCEPTIONS`,
`ROADMAP DONE` или `BLOCKED`. Статус `DONE` без префикса запрещён. Merge не
является доказательством GitHub Review.
