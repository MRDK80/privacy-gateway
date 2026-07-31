# Changelog

Все значимые изменения фиксируются в этом файле.
Формат: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Добавлено

- `route.json` версии `1.1` связывается с финальным `manifest.json` через поле `manifest_sha256`, содержащее SHA-256 всех байтов файла.
- Добавлена публичная функция `verify_manifest_integrity(route_data: dict, manifest_path: Path) -> None` для проверки согласованности пары артефактов.
- Проверка поддерживает `route.json` версии `1.0` без контрольной суммы для обратной совместимости и явно отклоняет неизвестные версии.
- Добавлены тесты создания и проверки контрольной суммы, включая обнаружение изменённого или взятого из другого запуска манифеста.

### Изменено

- Контрольная сумма вычисляется после атомарной записи `manifest.json` на финальный путь; после этого `route.json` записывается атомарно.
- Документация уточняет, что SHA-256 обеспечивает обнаружение рассинхронизации пары артефактов и повреждения файла манифеста, но без подписи не обеспечивает аутентичность и не является защитой от целенаправленной подмены.

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
  - Сообщения об ошибках без исходных значений

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

[0.2.0]: https://github.com/MRDK80/privacy-gateway/compare/v0.1.0...feat/e6-routing
[0.1.0]: https://github.com/MRDK80/privacy-gateway/commits/main
