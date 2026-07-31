# Архитектура Privacy Gateway

Документ описывает фактическое состояние кода после этапов Э1–Э6.

## Конвейер обработки

```
[входной текст: файл .txt / stdin]
        │
        ▼  input_parser.read_input()
           Читает файл или stdin с указанной кодировкой.
           Возвращает InputText{text, source, encoding, path}.
        │
        ▼  routing.load_routing_config(path)  [Э6]
           Загружает YAML-конфиг маршрутизации через yaml.safe_load.
           Возвращает RoutingConfig{output_dir, overwrite,
           tokenize_types, block_unconditionally}.
           Только безопасные умолчания при отсутствии файла.
        │
        ▼  keystore.get_key()
           Получает Fernet-ключ из системного keyring.
        │
        ▼  pipeline.prepare_pipeline(...)  [Э6]
           Сквозной конвейер:
           │
           ▼  detector.detect_entities(text, config)
              Применяет regex-паттерны и словарные записи из entities.yaml.
              Возвращает list[DetectedEntity] — без исходных значений,
              только fingerprint (SHA-256[:12]).
           │
           ▼  Фильтрация по RoutingConfig:
              block_unconditionally → немедленный BLOCKED.
              tokenize_types → только разрешённые типы передаются дальше.
           │
           ▼  tokenizer.tokenize(text, entities)
              Заменяет каждый span [start, end) на токен [TYPE_N].
              Возвращает (tokenized_text, list[TokenRecord]).
           │
           ▼  keystore.get_key()  ──►  crypto.encrypt(value, key)
              manifest.build_manifest(records, original_values, key)
              Шифрует каждое исходное значение Fernet-ключом.
              Возвращает list[ManifestEntry]{token, entity_type,
              fingerprint, encrypted_value (bytes)}.
           │
           ▼  manifest.save_manifest(entries, path)
              Записывает JSON-файл. encrypted_value — в hex.
              Права доступа: rw------- (только владелец).
              Исходные значения и ключ в файл не попадают.
           │
           ▼  validator.validate(tokenized_text)
              Два независимых прохода (без импорта detector.py):
              1. Негативный — email, IPv4/IPv6, телефоны,
                 secret_keyword, PEM, высокая энтропия (порог 3.0).
              2. Позитивный — формат [TYPE_N], вложенные/незакрытые
                 скобки, неизвестный тип токена.
              Возвращает ValidationResult{status, findings}.
           │
           ▼  При OK — атомарная запись артефактов:
              prompt.txt    — токенизированный текст (без исходных значений)
              route.json    — метаданные запуска и счётчики токенов
              manifest.json — зашифрованная карта token→value
        │
        ▼  PipelineResult{ status: OK | BLOCKED | PENDING,
                           prompt_path, route_path, manifest_path }
```

## Границы ответственности модулей

| Модуль | Ответственность | Этап |
|--------|-----------------|------|
| `models.py` | Все типизированные dataclass, StrEnum, исключения | Э2 |
| `input_parser.py` | Чтение файла/stdin, определение кодировки, валидация типа | Э2 |
| `detector.py` | Regex + словарь, разрешение перекрытий, приоритеты | Э3 |
| `crypto.py` | Fernet encrypt/decrypt/generate_key | Э4 |
| `keystore.py` | Хранение Fernet-ключа в системном keyring | Э4 |
| `tokenizer.py` | Замена span'ов на токены, стратегия присвоения | Э4 |
| `manifest.py` | Сборка, сохранение, загрузка зашифрованного манифеста | Э4 |
| `validator.py` | Независимая проверка выхода токенизатора | Э5 |
| `routing.py` | Загрузка и валидация YAML-конфига маршрутизации | Э6 |
| `pipeline.py` | Сквозной конвейер prepare (детектор→токенизатор→манифест→валидатор→артефакты) | Э6 |
| `cli.py` | Точка входа `pgw`, команды `detect` и `prepare` | Э1–Э6 |

## Модель данных

### EntityType
`PERSON`, `ROLE`, `ORG`, `DEPARTMENT`, `EMAIL`, `PHONE`, `HOST`, `ENDPOINT`,
`RESOURCE`, `SYSTEM`, `PROJECT`, `AMOUNT`, `METRIC`, `DOCUMENT`, `DATE`,
`DURATION`, `ENVIRONMENT`.

### DetectionConfidence
`HIGH` — regex с высокой точностью (email, URL, IP, PEM).  
`MEDIUM` — regex с возможными ложными срабатываниями (телефоны, суммы) или словарные совпадения.

### ProcessingStatus

| Значение | Семантика | Автоотправка |
|----------|-----------|-------------|
| `OK` | Текст токенизирован, остатков нет, все токены корректны | ✅ разрешена |
| `BLOCKED` | Остаточные PII/секреты или некорректные токены | ❌ запрещена |
| `PENDING` | Токен с неизвестным типом — нужно решение человека | ❌ запрещена |

**`PENDING` — не мягкий OK.** До явного одобрения оператора текст считается небезопасным.

### RoutingConfig  [Э6]

Конфигурация маршрутизации обработки:

| Поле | Тип | Умолчание | Описание |
|------|-----|-----------|----------|
| `output_dir` | str | `./pgw_out` | Каталог для артефактов |
| `overwrite` | bool | `false` | Перезаписывать ли существующие файлы |
| `tokenize_types` | list[str] | все EntityType | Типы для токенизации |
| `block_unconditionally` | list[str] | `[]` | Типы, блокирующие обработку |

### PipelineResult  [Э6]

Результат конвейера `prepare_pipeline`:

| Поле | Тип | Условие |
|------|-----|---------|
| `status` | ProcessingStatus | всегда |
| `message` | str | всегда (без исходных значений) |
| `prompt_path` | Path \| None | только при OK |
| `route_path` | Path \| None | только при OK |
| `manifest_path` | Path \| None | только при OK |
| `findings_summary` | list[dict] | тип+позиция, без значений |

### InputText
Хранит текст, источник (`file` / `stdin`), кодировку и опциональный путь. `repr` не раскрывает содержимое текста.

### DetectedEntity
Хранит тип, диапазон `[start, end)`, уверенность, источник (`regex` / `dictionary`) и fingerprint. Исходное значение **не хранится**.

### TokenRecord
Связывает токен (`[EMAIL_1]`) с fingerprint. Исходное значение не хранится.

### ManifestEntry
Содержит токен, тип сущности, fingerprint и `encrypted_value` (bytes). В файле — hex.

## Форматы артефактов  [Э6]

### prompt.txt
Токенизированный текст. Не содержит исходных значений.

### route.json
Метаданные конвейера. Не содержит исходных значений, ключа, соли, расшифрованных фрагментов.

```json
{
  "format_version": "1.0",
  "status": "OK",
  "timestamp": "<ISO-8601 UTC>",
  "source_ref": "<имя файла или stdin>",
  "manifest_path": "<путь к manifest.json>",
  "token_count": 3,
  "token_counts_by_type": {"EMAIL": 1, "HOST": 2},
  "entity_count_detected": 3,
  "entity_count_tokenized": 3
}
```

**`format_version`** — читается этапом Э7 для контроля совместимости.

### manifest.json
Зашифрованная карта token→value. Права доступа: rw------- (только владелец). Подробнее — в Э4.

## Криптографическая схема

**Алгоритм:** Fernet = AES-128-CBC + HMAC-SHA256 (аутентифицированное симметричное шифрование).

**Что шифруется:** исходные значения сущностей (каждое — отдельно).

**Что на диске:**
```
manifest.json  — token → encrypted_value (hex), fingerprint, entity_type
```

**Что НЕ на диске:** исходные значения в открытом виде, ключ шифрования.

**Где хранится ключ:** системный keyring.  
Service: `privacy-gateway`, Username: `fernet-key`.  
Байты ключа кодируются через `latin-1` для совместимости с keyring API.

**Валидация backend:** по полному имени класса (`module.qualname`) против allowlist:
- `keyring.backends.SecretService.Keyring`
- `keyring.backends.macOS.Keyring`
- `keyring.backends.Windows.WinVaultKeyring`

При небезопасном или недоступном backend — `KeystoreError` до любой операции.

**Порча шифртекста** детектируется: `Fernet.decrypt` поднимает `InvalidToken`, который транслируется в `DecryptionError`.

## Атомарность записи артефактов  [Э6]

Артефакты создаются **только при статусе OK**. При BLOCKED или PENDING ни одного файла не создаётся.

Запись `prompt.txt` и `route.json` выполняется через временный файл в той же директории с последующим `rename` (атомарная операция в пределах одной файловой системы).

## Почему валидатор независим от детектора

Детектор (Э3) настроен на отзыв с допустимым числом ложных срабатываний: его задача — найти как можно больше, не засоряя вывод. Валидатор (Э5) — независимый рубеж с противоположной задачей: **доказать отсутствие** остатков.

Асимметрия цены ошибки:
- Ошибка детектора (пропуск) → валидатор поймает → статус BLOCKED, утечки нет.
- Ошибка валидатора (пропуск) → утечка ПДн во внешнюю LLM.

Поэтому валидатор дублирует паттерны сознательно, не импортирует `detector.py` и не использует его конфигурацию. Независимость рубежей — это не дублирование ради дублирования, а защита от единой точки отказа.

## Кроссплатформенные решения

| Аспект | Решение |
|--------|----------|
| Пути в коде | Только `pathlib.Path` |
| Детектор RESOURCE | Распознаёт UNC, `C:\...` и `/opt/...` независимо от ОС |
| Кодировки | Явная передача: `utf-8`, `utf-8-sig`, `cp1251` |
| Переводы строк | Текстовый режим, универсальная обработка `\n` и `\r\n` |
| Права manifest.json | `chmod` с игнорированием ошибок на Windows |
| CI | Матрица `ubuntu-latest` + `windows-latest` |
