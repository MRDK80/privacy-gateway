# ADR-151: машиночитаемая интроспекция CLI-контракта

- Статус: принято
- Задача: #151 (epic #147)
- Зависимость: ADR-150 (JSON-контракт CLI, schema version `1.0`)

## Контекст

AI-агентам и автоматизации нужен машиночитаемый CLI-контракт без парсинга
README и человекочитаемого help. Операционный JSON-режим (#150) описывает
результат выполнения команды, но не структуру самого интерфейса.

Фактическое состояние parser на момент решения:

- `--json` — глобальный pre-parser control, обрабатываемый в `_parse_args()`
  до `argparse`; в `_build_parser()` он отсутствует;
- `pgw --help` и `pgw --json --help` дают побайтово одинаковый stdout,
  проверено sha256 всех восьми пар help-вызовов;
- `pgw --describe`, `pgw describe` и `pgw commands --json` до этого решения
  давали exit 3 (usage error) и пустой stdout.

## Решение

### 1. Invocation

Интроспекция вызывается как единственный аргумент:

```text
pgw --describe
```

Это pre-parser control, реализованный рядом с `--json` и обработанный в
`main()` до `argparse`.

### 2. Отклонённые альтернативы

- `pgw describe` как argparse subcommand: добавляет команду в список
  subcommands внутри `pgw --help` и изменяет его байты, нарушая критерий
  «существующие help не изменены».
- `pgw commands --json`: требует одновременно новый subcommand и
  переиспользование pre-parser флага, то есть двух изменений контракта.
- `pgw --json describe`: смешивает операционный envelope #150 и каталог,
  создавая неоднозначность двух schema version в одном режиме.

### 3. Catalog schema и version

Каталог — один JSON object, поле `schema_version` = `1.0`,
`kind` = `cli_catalog`, `program` = `pgw`.

Это версия каталога, независимая от версии пакета.

### 4. Связь с ADR-150

Каталог публикует отдельное поле
`operational_json_schema_version`, значение которого берётся из
`_JSON_SCHEMA_VERSION` (`1.0`). Версии каталога и операционного envelope
не смешиваются и могут расходиться в будущем.

### 5. Источник истины для синтаксиса

Команды, аргументы, option strings, `required`, `nargs`, `choices` и help
текст извлекаются рекурсивно из фактического `argparse.ArgumentParser`,
возвращаемого `_build_parser()`.

### 6. Источник истины для global `--json`

Pre-parser controls описаны структурированно в `_GLOBAL_CONTROLS` рядом с их
реализацией и публикуются в поле `global_controls`, а не как argparse
options.

### 7. Источник истины для machine codes

Поле `machine_error_codes` формируется из `_JSON_ERROR_CODES`.
Второй независимый реестр не создаётся.

### 8. Источник истины для exit codes

Глобальный реестр `_EXIT_CODE_REGISTRY` соответствует контракту
ADR-20/ADR-21/ADR-29/ADR-30: 0, 1, 2, 3, 4, 5. Новые коды не вводятся.

### 9. Representation команд и параметров

Команды публикуются как плоский список стабильных ID: `detect`, `prepare`,
`restore`, `key create`, `key status`, `key rotate`, плюс `path` в виде
массива сегментов. `key` — только namespace, самостоятельным узлом каталога
он не является. `pgw key delete` не существует и не публикуется.

### 10. Required/optional

`required` = `true` для positionals и для options с `required=True`
(`restore --route`). Остальные параметры — optional.

### 11. Side-effect taxonomy

Стабильный enum: `reads_artifacts`, `reads_config`, `reads_input`,
`reads_keyring`, `writes_artifacts`, `writes_keyring`, `writes_output`.
Значение означает возможный эффект, а не гарантию его наступления;
общая транзакционность артефактов не обещается.

### 12. Security и redaction

Каталог не содержит key material, PII, environment values, keyring backend
identifiers, service/username, локальных абсолютных путей и repr внутренних
объектов. `_MachineError` не сериализуется как Python object.

### 13. Детерминированность

Команды сортируются по ID, choices сортируются, arguments сохраняют порядок
объявления в parser, machine codes и exit codes сортируются. Timestamps,
случайные идентификаторы, cwd, home и платформозависимые значения
отсутствуют. Повторный вызов даёт побайтово идентичный вывод.

### 14. Compatibility и versioning

Обратно совместимые добавления полей не меняют `schema_version`.
Удаление или переопределение семантики поля требует новой major версии
каталога и отдельного ADR.

### 15. Unsafe defaults

Defaults публикуются по allowlist: только `false` для boolean-флагов.
Все остальные значения публикуются как `null`. Платформозависимый
`config.example/entities.yaml` не публикуется как default, поскольку
argparse-default этих параметров равен `None`.

### 16. Отсутствие operational side effects

`pgw --describe` не читает пользовательский вход, config, route, manifest,
не создаёт workspace, не пишет вывод и не обращается к keyring. Тесты
подменяют handlers и keystore на падающие функции и проверяют exit 0.

### 17. Взаимодействие с `--help` и `--json`

`pgw --help` и help всех subcommands не изменяются. `pgw --json --describe`
остаётся usage error и возвращает JSON envelope #150 с
`invalid_arguments` и exit 3. `pgw --describe extra` печатает безопасное
сообщение в stderr и возвращает exit 3.

### 18. Ограничения первой версии

- Machine codes и exit codes уровня команды курируются в typed registry
  `_COMMAND_FACTS`; тесты доказывают включение в глобальные реестры и
  полноту объединения, но не выводят их автоматически из кода команд.
- Каталог не описывает library API, MCP tools, shell completion и NDJSON.
- JSON-режим для `detect` не добавляется; в каталоге он честно указан как
  `output_formats: ["human"]`.
## Дополнение от 2026-09-10: usage-семантика exit codes (corrective)

Первая версия каталога перечисляла process exit codes команды вручную в
`_COMMAND_FACTS`. Это привело к доказанному расхождению: запись `key rotate`
публиковала `exit_codes = [0, 1, 4]` и одновременно machine code
`invalid_arguments`, тогда как malformed invocation
`pgw --json key rotate --unexpected` фактически завершается кодом 3.

Решение:

1. Usage error argparse — общая семантика каждой конечной команды, а не
   свойство отдельной команды. Каталог формирует её автоматически.
2. `_USAGE_EXIT_CODE = 3` добавляется к `exit_codes` каждой конечной
   команды при построении каталога.
3. `_USAGE_MACHINE_ERROR_CODE = "invalid_arguments"` автоматически
   добавляется в `machine_error_codes` каждой команды, поддерживающей
   JSON-режим.
4. `detect` публикует exit code 3, но не публикует machine codes: JSON-режим
   для него не поддержан, поэтому `unsupported_command` относится к уровню
   CLI, а не к команде.
5. `_COMMAND_FACTS` содержат только операционные коды и не дублируют
   universal usage-семантику; contract test запрещает присутствие
   `invalid_arguments` в typed registry.

Проверки:

- parameterized test выполняет malformed human invocation для всех шести
  конечных команд и требует exit 3 и наличия кода 3 в каталоге;
- parameterized test выполняет malformed JSON invocation для всех пяти
  JSON-supported команд и сверяет exit code, machine code, command ID,
  пустой stderr и содержимое catalog entry;
- отдельный regression test закрывает исходный случай
  `pgw --json key rotate --unexpected`;
- semantic invariant: наличие `invalid_arguments` в каталоге требует
  присутствия exit code 3;
- покрытие проверяется сверкой набора argv с фактическим обходом parser и с
  `_JSON_COMMANDS`, поэтому новая команда не останется без теста.

Registry больше не проверяет сам себя: источником истины для exit code
выступает фактический вызов CLI.

`schema_version` каталога остаётся `1.0`: набор полей и их типы не
изменились, изменилось только содержимое отдельных значений, которое ранее
расходилось с фактическим поведением. Process exit codes продуктом не
добавлялись и не изменялись; operational JSON контракт ADR-150 не менялся.
