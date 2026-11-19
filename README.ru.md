# Privacy Gateway

[English](README.md) | [Русский](README.ru.md)

Translation of: README.md  
Reviewed against commit: 9f0f1a93203940b84c5aa48a442506011a243d30  
Translation status: current

Локальный CLI-инструмент и библиотека для безопасной подготовки корпоративных
текстов перед ручной передачей во внешнюю языковую модель. Работает полностью
локально: обнаруживает ПДн и секреты, заменяет их токенами вида `[TYPE_N]`,
шифрует карту соответствий, проверяет подготовленный текст и восстанавливает
исходные значения после получения ответа LLM.

Английский README — каноническая точка входа в рамках поэтапной миграции.
Связанные руководства и сообщения CLI пока могут оставаться на русском;
[языковая policy](docs/LANGUAGE_POLICY.md) определяет границы миграции.
Ссылка на документ не означает, что он уже переведён.

## Статус

Реализованы этапы Э1–Э8 и стабильный библиотечный API: локальная детекция,
псевдонимизация, подготовка артефактов, независимая валидация, строгое
восстановление ответа и управление ключом. Пакет объявлен как
`Development Status :: 3 - Alpha`. История изменений — в
[`CHANGELOG.md`](CHANGELOG.md).

<!-- pgw-section: protection-boundary -->
## Границы защиты

Инструмент выполняет псевдонимизацию, а не гарантированную анонимизацию.

Что он даёт:

- исходные значения шифруются локально; открытых значений нет в
  `manifest.json`, `route.json` и служебном отчёте;
- ответ внешней модели считается недоверенным входом: `restore` классифицирует
  токены и по умолчанию работает в строгом режиме;
- контекст восстановления, манифест и ключевой материал не передаются
  внешнему обработчику; отправляйте только предназначенный ему защищённый
  текст, а не весь подготовленный payload;
- `manifest_sha256` в `route.json` связывает пару артефактов и обнаруживает их
  рассинхронизацию.

Чего он не даёт:

- детекция ограничена реализованными regex и словарями, поэтому неизвестные
  классы данных могут остаться необнаруженными;
- многострочные секреты и пароли вне keyword-паттернов не распознаются;
- косвенная идентификация по комбинации обезличенных полей не предотвращается;
- SHA-256 без подписи не защищает от согласованной подмены обоих артефактов;
- результат `restore` содержит открытый текст и требует той же защиты, что и
  исходный документ.

Полная модель угроз — [`docs/SECURITY.md`](docs/SECURITY.md), краткая
навигация — [`docs/threat-model.md`](docs/threat-model.md). Поддерживаемые
сущности и ограничения детектора — [`docs/detection.md`](docs/detection.md).

## Требования

- Python 3.11 или новее; в CI поддерживаются и проверяются 3.11 и 3.12
- Linux: `gnome-keyring` или другой SecretService-совместимый демон
- macOS: Keychain (backend поддерживается; macOS не входит в CI matrix)
- Windows: Windows Credential Vault

Ключ шифрования хранится только в системном keyring. В headless-среде нужна
активная сессия keyring (`dbus-run-session` и `gnome-keyring-daemon`);
подробности — в [`docs/SECURITY.md`](docs/SECURITY.md).

Backend выбирается средствами библиотеки `keyring`: Privacy Gateway использует
`keyring.get_keyring()` и проверяет выбранный класс по allowlist безопасных
системных backend до обращения к ключам. Переменная `PGW_KEYRING_BACKEND`
в Privacy Gateway не поддерживается.

<!-- pgw-section: installation -->
## Установка

Для CLI используйте pipx с Python 3.11/3.12. До отдельно подтверждённой PyPI
публикации устанавливайте проверенный исходный commit, а не одноимённый пакет
из index. Если pipx уже установлен (Linux с доступным `python3.12`):

```bash
pipx install --python python3.12 "git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843"
pgw --help
pgw doctor
```

Если `pgw` ещё не доступен в PATH, используйте путь, показанный pipx.
[Полная инструкция](docs/INSTALLATION.md) содержит отдельные Linux/Windows
PowerShell команды без изменения shell profile, bootstrap pipx, Library API
через venv/pip, обновление, удаление и безопасную диагностику.

Для разработки и quality gate используйте [CONTRIBUTING.md](CONTRIBUTING.md).
Карта зависимостей и единственный extra `dev` — в
[DEPENDENCIES.md](docs/DEPENDENCIES.md). Продуктовых NER/proxy/provider extras нет.

## Первый запуск

Пример ниже запускается из отдельного checkout репозитория с каталогом
`examples`; pipx и wheel не устанавливают repository examples. См.
[инструкцию установки](docs/INSTALLATION.md) для выбора CLI и Library API
окружения. Для CLI key-команды используйте установленный `pgw`, а Python-пример
выполняйте интерпретатором venv с установленным Library API.

```bash
pgw key create
python examples/01_quickstart.py
```

`pgw key create` создаёт активный ключ в системном keyring. Без ключа пример
завершится ошибкой хранилища — это ожидаемое fail-closed поведение. Пример
выполняет цикл `prepare` -> локальный детерминированный ответ -> `restore` ->
`discard`, не обращается к сети и использует только синтетические данные.

Ожидаемый вывод:

```text
tokens_prepared=<N>
tokens_restored=<N>
tokens_missing=0
protected_leak_free=True
roundtrip_exact=True
workspace_clean=True
```

## CLI

| Команда | Назначение |
|---------|------------|
| `pgw detect` | Диагностика: метаданные найденных сущностей без исходных значений |
| `pgw prepare` | Подготовка `prompt.txt`, `manifest.json` и `route.json` |
| `pgw restore` | Восстановление исходных значений из ответа LLM |
| `pgw key` | Управление ключом: `create`, `status`, `rotate` |

Минимальный сквозной цикл:

```bash
pgw prepare input.txt --out ./pgw_out
# передать содержимое ./pgw_out/prompt.txt во внешнюю модель,
# ответ сохранить в llm_reply.txt
pgw restore llm_reply.txt --route ./pgw_out/route.json --out restored.txt
```

Строгий режим включён по умолчанию: неизвестный или искажённый токен вызывает
отказ с кодом 5, и файл результата не создаётся. Мягкий режим включается
только явным `--lenient` и не делает ответ модели доверенным.

Единственный источник актуального синтаксиса команд и параметров —
`pgw --describe`; интерактивные подсказки дают `pgw --help` и
`pgw <команда> --help`. Проверяемый сквозной сценарий с флагами, кодами
завершения и различиями Bash и PowerShell —
[`examples/06_cli_round_trip.md`](examples/06_cli_round_trip.md). Ротация
ключа — [`examples/05_key_rotation.md`](examples/05_key_rotation.md). Коды
возврата, режимы восстановления и классификация токенов —
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

<!-- pgw-section: library -->
## Библиотечное использование

Ниже схема для уже настроенного keyring с существующим ключом. `text` и
`external_processor` предоставляет приложение.

```python
from privacy_gateway import GatewayConfig, PrivacyGateway

gateway = PrivacyGateway(GatewayConfig())

prepared = gateway.prepare(text, correlation_id="req-1")
response_text = external_processor(prepared.text)
restored = gateway.restore(response_text, context=prepared.context)

gateway.discard(prepared.context)
```

`prepare` возвращает защищённый текст и непрозрачный контекст восстановления.
`restore` проверяет целостность защищённых артефактов до возврата открытого
текста. Артефакты удаляет потребитель вызовом `discard`. Полный контракт —
[`docs/LIBRARY_API.md`](docs/LIBRARY_API.md), формат токенов —
[`docs/token-format.md`](docs/token-format.md).

Схема не гарантирует успешную очистку при отказе приложения; ограничения
cleanup и остальные гарантии определены в библиотечном контракте.

## Примеры

Воспроизводимые сценарии и требования к их запуску собраны в
[`examples/README.md`](examples/README.md): минимальный round-trip, полный
цикл с несколькими типами сущностей, блокировка секрета, недоверенный ответ
модели, ротация ключа и сквозной сценарий CLI.

<!-- pgw-section: limitations -->
## Известные ограничения

- Глубина ротации ограничена одним retired-ключом (ADR-23): после второй
  ротации манифесты самого старого поколения не читаются.
- Токены не стабильны между запусками: нумерация ведётся внутри одного
  документа, поэтому `[EMAIL_1]` в разных запусках может означать разные
  значения.
- `pgw detect` — диагностика, а не разрешение на отправку исходного текста.
- Статус `PENDING` не является мягким `OK` и требует ручного одобрения.
- Защита от TOCTOU при перезаписи требует отдельного подтверждения тестами и
  аудитом реализации.

## Разработка

Обязательный локальный quality gate, модель ветвления, политика версий Python
и требования к отчётности — в [`CONTRIBUTING.md`](CONTRIBUTING.md).

Не добавляйте в репозиторий реальные корпоративные данные. В примерах
используйте только синтетические значения: `user@example.com`, `192.0.2.10`,
`2001:db8::1`, `+7 900 000-00-00`. Политика репозитория и порядок сообщения об
уязвимостях — [`SECURITY.md`](SECURITY.md); техническая модель угроз —
[`docs/SECURITY.md`](docs/SECURITY.md).

## Документация

| Документ | Содержание |
|----------|------------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Конвейер, модель данных, форматы артефактов, коды возврата |
| [`docs/LIBRARY_API.md`](docs/LIBRARY_API.md) | Публичный API, жизненный цикл контекста, исключения |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Модель угроз, границы доверия, обращение с ключом |
| [`docs/detection.md`](docs/detection.md) | Поддерживаемые сущности и ограничения детектора |
| [`docs/token-format.md`](docs/token-format.md) | Синтаксис токенов, нумерация, поведение при восстановлении |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Журнал архитектурных решений (ADR) |
| [`examples/README.md`](examples/README.md) | Индекс воспроизводимых примеров |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Definition of Done, quality gate, модель ветвления |

Языковая модель и правила синхронизации — в
[Language policy](docs/LANGUAGE_POLICY.md).

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
