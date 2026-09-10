# ADR-150: стабильный JSON-контракт CLI

- Статус: Accepted
- Дата: 2026-09-10
- Issue: [#150](https://github.com/MRDK80/privacy-gateway/issues/150)
- Epic: [#147](https://github.com/MRDK80/privacy-gateway/issues/147)
- Связанные решения: ADR-15, ADR-16, ADR-20, ADR-21, ADR-29, ADR-30,
  ADR-31 и ADR-47 из [реестра решений](DECISIONS.md)

## Контекст

Автоматизации нужен однозначный машиночитаемый результат `pgw`. Существующий
вывод предназначен для человека и является стабильным контрактом: его строки,
stdout/stderr и process exit codes нельзя менять. Интроспекция CLI относится к
отдельной задаче #151.

`restore` создаёт особый риск: восстановленный текст снова чувствителен и не
может находиться в служебном JSON. `prepare` не должен сериализовать внутренние
объекты, manifest contents или исходные значения.

## Решение

JSON-режим включается только глобальным префиксом перед командой:

```text
pgw --json prepare ...
pgw --json restore ... --out RESULT
pgw --json key create
pgw --json key status
pgw --json key rotate
```

Форма `pgw prepare --json ...` не поддерживается и остаётся обычной ошибкой
argparse с process exit code 3. Parser и вывод `--help` для прежних вызовов не
меняются.

Поддерживаются `prepare`, `restore`, `key create`, `key status` и `key rotate`.
`detect` исследована, но исключена: текущий stdout содержит подробные находки,
а согласование их безопасной схемы не входит в #150. `pgw --json detect ...`
возвращает `unsupported_command` и код 3. `key delete` не существует.

## Envelope

Начальная версия схемы — строка `"1.0"`. Успех:

```json
{"schema_version":"1.0","ok":true,"command":"prepare","result":{"status":"ok"}}
```

Ошибка:

```json
{"schema_version":"1.0","ok":false,"command":"restore","error":{"code":"restore_output_required","type":"usage_error","message":"JSON-режим restore требует --out."}}
```

Обязательны `schema_version`, `ok`, `command` и ровно одно из `result` или
`error`. Ошибка содержит строковые `code`, `type`, `message`. Каждый operational
вызов пишет один JSON object и завершающий перевод строки.

## Success results

Используется строгий allowlist:

| Command | `result` |
|---|---|
| `prepare` | `status: "ok"` |
| `restore` | `status: "ok"`, `output_path: string` |
| `key create` | `created: true` |
| `key status` | `present: true` |
| `key rotate` | `rotated: true` |

`prepare` не раскрывает абсолютные пути, findings, manifest contents или
исходные значения. Артефакты и atomic/no-clobber semantics остаются прежними.

`restore --json` требует `--out` и проверяет его до чтения ответа LLM.
Восстановленный текст записывается существующим `write_restored`; stdout
содержит только envelope. `output_path` — ровно лексическая строка пользователя
без `resolve()` и раскрытия других путей. POSIX и Windows-style пути остаются
строками JSON.

Key-команды не возвращают Fernet key, retired key, backend, service/username
или внутренние identifiers. Успех ротации не обещает транзакцию или rollback
сверх существующей state-machine semantics.

## Machine error codes

Machine codes не заменяют process exit codes и не создают новых кодов:

| Machine code | Type | Exit | Назначение |
|---|---|---:|---|
| `invalid_arguments` | `usage_error` | 3 | Ошибка argparse в JSON-режиме |
| `unsupported_command` | `usage_error` | 3 | Команда вне JSON-контракта |
| `restore_output_required` | `usage_error` | 3 | Нет `--out` для restore |
| `input_error` | `input_error` | 3 | Ошибка чтения |
| `configuration_error` | `configuration_error` | 3 | Ошибка конфигурации |
| `pending` | `processing_state` | 2 | Требуется подтверждение |
| `blocked` | `processing_state` | 3 | Политика заблокировала обработку |
| `output_error` | `output_error` | 3 | Collision или отказ записи |
| `restore_error` | `restore_error` | 3 | Ошибка восстановления |
| `strict_restore_error` | `restore_error` | 5 | Строгий отказ по токенам |
| `key_exists` | `keystore_error` | 3 | Повторное создание ключа |
| `key_not_found` | `keystore_error` | 3 или 4 | Ключ отсутствует |
| `keystore_error` | `keystore_error` | 4 | Прочий отказ keyring/rotation |
| `internal_error` | `internal_error` | 1 | Непредвиденная ошибка |

Различие 3 и 4 для `key_not_found` сохраняет текущую семантику: `key status`
возвращает 3, а keystore failure в остальных командах — 4. PENDING сохраняет
2; argparse usage error остаётся 3.

## stdout, stderr и parser

При активном JSON-режиме stdout содержит только envelope, stderr пуст.
Человекочитаемый вывод команды перехватывается и наружу не публикуется. Текст
исключения никогда не копируется в JSON. Unexpected exception становится
фиксированным `internal_error`; traceback не печатается.

Machine code, public type и безопасное сообщение формируются как внутренний
структурированный `_MachineError`. Exception rules и явные command states
записывают эту семантику независимо от human renderer. JSON adapter не читает и
не анализирует stdout/stderr, локализованные prefixes или текст исключения.
Изменение либо перевод human-readable сообщения поэтому не меняет machine code.
Если команда завершилась ненулевым кодом без ровно одного структурированного
machine error, adapter fail-closed возвращает `internal_error` с process code 1.

Argparse errors при префиксе `--json` подавляют usage/error и возвращают
безопасный `invalid_arguments`, не сериализуя argv. Без префикса поведение
argparse побайтово прежнее.

`--help` — управляющее действие parser, не operational invocation. `pgw --help`,
`pgw --json --help` и help подкоманд остаются человекочитаемыми и возвращают 0.
JSON-интроспекция help относится к #151.

## Encoding и versioning

JSON выводится как одна UTF-8-совместимая строка без ASCII escaping и без
timestamps. Пути имеют тип string; числовые и логические значения не
преобразуются в строки.

Consumer обязан игнорировать неизвестные поля. Добавление необязательного поля
совместимо в `1.x`. Удаление поля, изменение типа/семантики/обязательности,
переименование machine code или envelope — breaking change и требует новой
major schema version. Порядок JSON keys не является контрактом.

## Почему нет TTY auto-detection

Формат выбирает только явный флаг. TTY detection сделала бы invocation
зависимым от окружения, осложнила pipelines и могла неожиданно изменить
существующий вывод.

## Последствия

- Human mode без `--json` сохраняет stdout, stderr и exit codes.
- Бизнес-операция выполняется один раз; human и JSON renderers получают одну
  структурированную machine-error semantics.
- Все объявленные machine codes имеют достижимый scenario и contract test;
  недостижимый fallback `command_error` удалён до release.
- Library API не меняется.
- NDJSON, streaming, MCP, introspection и JSON для `detect` вне scope.
