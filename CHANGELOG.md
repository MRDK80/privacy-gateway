# Changelog

Все значимые изменения фиксируются в этом файле.
Формат: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

[0.1.0]: https://github.com/MRDK80/privacy-gateway/commits/main
