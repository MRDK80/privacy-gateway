# Changelog

Все значимые изменения фиксируются в этом файле.
Формат: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

- **Изменено (поведение CLI):** ошибка разбора аргументов командной строки
  завершает процесс кодом 3 вместо 2; код 2 закреплён только за PENDING
  (#26, ADR-29). Текст usage/error в stderr, команды, флаги и остальные
  коды завершения не изменились; `--help` по-прежнему даёт код 0.

## [0.3.0] — 2026-08-15

### Добавлено (#14 — библиотечный API)

- `facade.py` — публичный фасад `PrivacyGateway` с методами `prepare`, `restore`
  и `discard` поверх существующего конвейера, без дублирования детекции,
  токенизации, шифрования и manifest.
- Публичные модели `GatewayConfig`, `PreparedPayload`, `RestoredPayload` и
  непрозрачный `RestoreContext` с сериализацией через `to_token`/`from_token`.
- `exceptions.py` — стабильная иерархия: `PrivacyGatewayError`,
  `ConfigurationError`, `DetectionError`, `KeyStoreError`, `IntegrityError`,
  `RestoreError`, `StrictTokenError`.
- Стабильный публичный путь импорта через `privacy_gateway.__all__` и маркер
  `py.typed` в поставке пакета.
- `docs/LIBRARY_API.md` — контракт, жизненный цикл контекста,
  потокобезопасность, правила очистки и fail-closed.
- Тесты: `test_public_contract.py`, `test_facade_prepare.py`,
  `test_facade_restore.py` — 51 тест, включая round-trip, Unicode, несколько
  значений, недействительный контекст, повреждение артефактов, отсутствие
  ключа и строгий режим.
- ADR-26, ADR-27, ADR-28 в `docs/DECISIONS.md`.

### Не изменено (#14)

- `cli.py`, `pipeline.py`, `restore.py`, `manifest.py`, `keystore.py`,
  `routing.py` и `models.py` не затронуты: команды, параметры и коды
  завершения 0/1/2/3/4/5 сохранены.

### Добавлено (Э8 — ротация ключей и MultiFernet)

- `crypto.py` — расширен поддержкой MultiFernet:
  - `encrypt_multi(plaintext, keys)` — шифрует первым ключом списка (`keys[0]`);
  - `decrypt_multi(ciphertext, keys)` — перебирает ключи по очереди до первого успешного расшифрования;
  - при исчерпании всех ключей поднимает `DecryptionError`.

- `keystore.py` — расширен поддержкой ротации:
  - `rotate_key()` — создаёт новый active-ключ, сохраняет текущий как retired; сначала записывает retired, затем active (атомарность в смысле keystore);
  - `get_all_keys()` — возвращает `[active_key, retired_key]`; новый ключ первым гарантирует шифрование новых манифестов активным ключом;
  - `delete_key()` — удаляет active и retired ключи; операция необратима.

- Команда `pgw key` с тремя подкомандами:
  - `pgw key create` — создать новый активный ключ (ошибка, если ключ уже существует);
  - `pgw key rotate` — ротировать ключ (старый сохраняется как retired, новый становится active);
  - `pgw key delete` — удалить ключ с предупреждением о необратимости; требует `--yes` в интерактивном режиме.

- `restore_text` теперь использует `get_all_keys()` + `decrypt_multi`:
  - манифесты, созданные до последней ротации, читаются без ручных действий (ADR-23);
  - новые манифесты шифруются активным ключом.

- Разведён код возврата 5 для строгого токенного отказа:
  - `RestoreStrictError` — отдельный класс исключения (не наследник `RestoreError`);
  - CLI отлавливает `RestoreStrictError` явно и завершается с кодом 5;
  - ошибки конфигурации/целостности по-прежнему возвращают код 3.

- `tests/test_key_rotation.py` — тесты ротации и жизненного цикла ключей:
  - после `rotate_key()` активный ключ новый; старый не расшифрует новый шифртекст;
  - манифест, созданный до ротации, читается через `get_all_keys()` + `decrypt_multi` (ADR-23);
  - нарушение порядка ключей в MultiFernet обнаруживается при шифровании;
  - прерывание `rotate_key` до завершения второй записи оставляет active без изменений;
  - удаление ключа делает данные нечитаемыми (`KeyNotFoundError`, `DecryptionError`);
  - полный цикл `create → prepare → restore → rotate → restore` старого манифеста;
  - коды 3, 4, 5 различимы между собой.

### Добавлено (Э7 — восстановление текста)

- `restore.py` — сквозное восстановление исходных значений в ответе LLM:
  - загрузка и проверка `route.json`;
  - выбор `manifest_path` с поддержкой явного `--manifest`;
  - разрешение относительного пути от каталога `route.json`;
  - проверка целостности до обращения к keyring и расшифровки;
  - загрузка и расшифровка `manifest.json`;
  - подстановка всех известных токенов;
  - формирование отчёта без исходных и восстановленных значений.

- Команда `pgw restore`:
  - ответ LLM читается из файла или stdin;
  - `--route` задаёт обязательный путь к `route.json`;
  - `--manifest` переопределяет путь к манифесту;
  - `--out` записывает восстановленный текст в файл;
  - `--overwrite` разрешает замену существующего результата;
  - `--lenient` явно включает мягкий режим;
  - без `--out` восстановленный текст выводится в stdout;
  - служебный отчёт без plaintext выводится отдельно.

- Классификация токенов в недоверенном ответе LLM:
  - известные токены восстанавливаются;
  - неизвестные токены вызывают отказ в строгом режиме;
  - искажённые кандидаты вызывают отказ в строгом режиме;
  - пропавшие токены включаются в отчёт без произвольной подстановки;
  - дублированные известные токены заменяются во всех вхождениях;
  - регистр токенов не нормализуется автоматически.

- Строгий режим включён по умолчанию. Мягкий режим доступен только через
  явный флаг `--lenient`.

- Атомарная запись восстановленного результата через временный файл в
  каталоге назначения и `os.replace`.

- Добавлены модели результата восстановления и безопасного отчёта без
  расшифрованных значений.

- Добавлены тесты Э7:
  - точный и Unicode round-trip `prepare → restore`;
  - чтение ответа LLM из stdin;
  - строгий отказ для неизвестных и искажённых токенов;
  - мягкий режим только по явному флагу;
  - отчёт о пропавших и дублированных токенах;
  - поддержка `route.json` версий `1.0` и `1.1`;
  - отказ для неизвестной версии;
  - проверка целостности до keyring и расшифровки;
  - обнаружение подменённого или повреждённого манифеста;
  - разрешение относительного `manifest_path`;
  - читаемые ошибки чужого или отсутствующего ключа;
  - отсутствие plaintext в служебном отчёте;
  - отсутствие выходного файла при ошибке;
  - защита существующего результата без `--overwrite`.

### Добавлено (целостность артефактов)

- `route.json` версии `1.1` связывается с финальным `manifest.json` через
  поле `manifest_sha256`, содержащее SHA-256 всех байтов файла.
- Добавлена публичная функция
  `verify_manifest_integrity(route_data: dict, manifest_path: Path) -> None`.
- Версия `route.json` `"1.0"` поддерживается без контрольной суммы для
  обратной совместимости.
- Версия `"1.1"` требует корректного `manifest_sha256`.
- Неизвестные версии, отсутствующий файл и несовпадение суммы отклоняются
  через `ConfigurationError`.

### Изменено

- Контрольная сумма вычисляется после атомарной записи `manifest.json` на
  финальный путь; затем атомарно записывается связанный `route.json`.
- `verify_manifest_integrity` вызывается для фактически выбранного
  манифеста до keyring, загрузки и расшифровки.
- Код возврата 3 также используется для ошибки целостности и строгого
  отказа `restore`; ошибка keyring по-прежнему возвращает код 4.
- Документация обновлена для Э7:
  - `README.md` описывает команду и цикл `prepare → restore`;
  - `docs/ARCHITECTURE.md` описывает поток восстановления;
  - `docs/SECURITY.md` рассматривает ответ LLM как недоверенный вход;
  - `docs/DECISIONS.md` фиксирует архитектурные решения Э7.
- Уточнено, что SHA-256 обнаруживает рассинхронизацию и повреждение, но без
  подписи не обеспечивает аутентичность и не защищает от согласованной
  подмены обоих файлов.

### Изменено (Э8)

- `restore_text` использует `get_all_keys()` + `decrypt_multi` вместо
  одиночного `get_key()` — обратная совместимость манифестов после ротации (ADR-23).
- Код возврата 5 выделен для строгого токенного отказа (`RestoreStrictError`);
  код 3 остаётся для ошибок конфигурации и целостности (ADR-21).
- `docs/DECISIONS.md` обновлён: добавлены ADR-21…ADR-25; ADR-06 переведён
  в статус «действует» (MultiFernet реализован).

## [0.2.0] — Этап Э6

### Добавлено (Э6 — YAML-маршрутизация и конвейер prepare)

- `routing.py` — загрузка и валидация YAML-конфига маршрутизации:
  - `load_routing_config(path: Path | None) -> RoutingConfig`
  - Загрузка только через `yaml.safe_load` (защита от deserialization-атак)
  - Явная ошибка при неизвестных ключах верхнего уровня и в секции `rules`
  - Явная ошибка при конфликте `tokenize` / `block_unconditionally`
  - Безопасные умолчания при отсутствии файла (не ошибка)

- `pipeline.py` — сквозной конвейер подготовки текста:
  - `prepare_pipeline(text, source_ref, routing_cfg, key, out_dir, overwrite) -> PipelineResult`
  - Порядок: детектор → фильтрация по RoutingConfig → токенизатор → манифест → валидатор → запись артефактов
  - Атомарная запись `prompt.txt` и `route.json` через временный файл + `rename`
  - Артефакты создаются **только** при статусе OK
  - `route.json` содержит `format_version: "1.0"` для Э7
  - `manifest.json` создаётся с правами `rw-------`
  - `PipelineResult` — публичный контракт результата конвейера

- `cli.py` — полная реализация команды `pgw prepare`:
  - Флаги: `--out`, `--routing`, `--config`, `--encoding`, `--overwrite`
  - Коды завершения: 0=OK, 2=PENDING, 3=BLOCKED/config, 4=keystore, 1=unexpected

- `tests/test_routing.py` — тесты YAML-маршрутизации:
  - Защита от YAML-инъекции (`!!python/object/apply`)
  - Неизвестные ключи верхнего уровня и в rules
  - Конфликт tokenize/block_unconditionally
  - Битый YAML, отсутствующий файл, None-путь
  - Валидный конфиг, невалидный тип сущности

- `tests/test_cli_prepare.py` — сквозные тесты команды prepare:
  - Создание артефактов при OK
  - `prompt.txt` не содержит исходных значений
  - `route.json` не содержит чувствительных данных, содержит `format_version`
  - `manifest.json` создан и зашифрован
  - Режим stdin (`source_ref="stdin"`)
  - BLOCKED: ненулевой код, файлы не созданы, сообщение без утечки значений
  - PENDING: код отличается от BLOCKED
  - Пустой ввод, защита от перезаписи без `--overwrite`
  - Понятное сообщение при `KeyNotFoundError`

### Обновлено (Э6)
- `docs/ARCHITECTURE.md` — добавлены `routing.py`, `pipeline.py`, форматы артефактов, атомарность записи
- `docs/DECISIONS.md` — добавлены ADR-10 (safe_load), ADR-11 (fail closed конфиг), ADR-12 (атомарность), ADR-13 (format_version)
- `docs/SECURITY.md` — добавлены разделы по безопасности YAML-конфига и содержимому route.json
- `README.md` — статус Э6 обновлён, добавлен раздел по команде prepare

## [0.1.0] — Этапы Э1–Э5

### Добавлено (Э1 — каркас)
- Структура проекта: `src/privacy_gateway/`, `tests/`, `docs/`, `config.example/`
- `pyproject.toml` с зависимостями (`cryptography`, `keyring`, `PyYAML`) и dev-зависимостями
- CI: GitHub Actions — тесты на `ubuntu-latest` + `windows-latest`
- CI: отдельный workflow `pre-commit` со сканированием секретов (`detect-secrets` v1.5.0)
- `.secrets.baseline` с 2 известными записями (`is_secret: false`) в `tests/test_detector.py`
- `SECURITY.md` — политика безопасности репозитория
- Примеры конфигурации в `config.example/entities.yaml`
- `cli.py` — точка входа `pgw`, заглушки команд `prepare` и `restore`

### Добавлено (Э2 — чтение входа)
- `models.py` — все типизированные модели данных: `EntityType`, `DetectionConfidence`,
  `InputSource`, `ProcessingStatus`, `InputText`, `DetectedEntity`, `TokenRecord`,
  `ManifestEntry`; исключения `InputError`, `UnsupportedInputError`, `EncodingError`,
  `ConfigurationError`
- `input_parser.py` — безопасное чтение файла или stdin, явная кодировка
- `cli.py` — команда `pgw detect` c флагами `--encoding` и `--config`
- Кроссплатформенное чтение путей: UNC, `C:\...`, POSIX `/opt/...`

### Добавлено (Э3 — детектор)
- `detector.py` — regex-паттерны: EMAIL, PHONE, HOST (IPv4), ENDPOINT (URL),
  RESOURCE (пути), DATE, AMOUNT, секреты (PASSWORD, API_TOKEN, PRIVATE_KEY,
  CONNECTION_STRING)
- Словарный детектор (PERSON, ORG, SYSTEM, PROJECT, DEPARTMENT, ROLE, ENVIRONMENT)
- Таблица приоритетов перекрытий (`_TYPE_PRIORITY`, `_SECRET_KIND_PRIORITY`)
- `DetectorConfig` и `load_config()` для загрузки конфигурации из YAML
- Детерминированное разрешение перекрытий (`_resolve_overlaps`)

### Добавлено (Э4 — токенизация и шифрование)
- `crypto.py` — `encrypt`, `decrypt`, `generate_key`, `DecryptionError`; Fernet
- `keystore.py` — `get_key`, `create_key`, `delete_key`, `KeystoreError`,
  `KeyNotFoundError`; валидация backend по allowlist; поддержка `PGW_KEYRING_BACKEND`
- `tokenizer.py` — `tokenize`, `PerDocumentStrategy`, `TokenAssignmentStrategy`
  (Protocol); вариант A (per-document счётчик)
- `manifest.py` — `build_manifest`, `save_manifest`, `load_manifest`,
  `decrypt_manifest_entry`; JSON-формат, `encrypted_value` в hex
- Исправлен дефект приёмки Э4 (коммит `2704d6d`)

### Добавлено (Э5 — валидатор)
- `validator.py` — `validate`, `ValidationResult`, `ValidationFinding`
- Негативные проверки: email, IPv4, IPv6, телефоны, secret_keyword, PEM,
  высокоэнтропийные строки (порог Шеннона 3.0, минимальная длина 16 символов)
- Позитивные проверки: формат `[TYPE_N]`, вложенные/незакрытые скобки,
  неизвестный тип токена
- Семантика `PENDING` задокументирована в `models.py` и `validator.py`
- `ProcessingStatus` в `models.py` расширен docstring'ом с полной семантикой

[0.3.0]: https://github.com/MRDK80/privacy-gateway/commits/main
[0.2.0]: https://github.com/MRDK80/privacy-gateway/commits/main
[0.1.0]: https://github.com/MRDK80/privacy-gateway/commits/main
