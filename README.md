# Privacy Gateway

Локальный CLI-инструмент и библиотека для безопасной подготовки корпоративных
текстов перед ручной передачей во внешнюю языковую модель. Работает полностью
локально: обнаруживает ПДн и секреты, заменяет их токенами вида `[TYPE_N]`,
шифрует карту соответствий, проверяет подготовленный текст и восстанавливает
исходные значения после получения ответа LLM.

## Статус

Реализованы этапы Э1–Э8 и стабильный библиотечный API: локальная детекция,
псевдонимизация, подготовка артефактов, независимая валидация, строгое
восстановление ответа и управление ключом. Пакет объявлен как
`Development Status :: 3 - Alpha`. История изменений — в
[`CHANGELOG.md`](CHANGELOG.md).

## Границы защиты

Инструмент выполняет псевдонимизацию, а не гарантированную анонимизацию.

Что он даёт:

- исходные значения шифруются локально; открытых значений нет в
  `manifest.json`, `route.json` и служебном отчёте;
- ответ внешней модели считается недоверенным входом: `restore` классифицирует
  токены и по умолчанию работает в строгом режиме;
- контекст восстановления, манифест и ключевой материал не передаются
  внешнему обработчику;
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
- macOS: Keychain
- Windows: Windows Credential Vault

Ключ шифрования хранится только в системном keyring. В headless-среде нужна
активная сессия keyring (`dbus-run-session` и `gnome-keyring-daemon`);
подробности — в [`docs/SECURITY.md`](docs/SECURITY.md).

## Установка

```bash
git clone https://github.com/MRDK80/privacy-gateway.git
cd privacy-gateway
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

В Windows PowerShell используйте `py -3.11 -m venv .venv` и
`.venv\Scripts\Activate.ps1`, далее та же команда установки. Варианты
developer-окружения, включая единую установку всех инструментов quality gate,
и политика поддерживаемых версий Python — в
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Первый запуск

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

Актуальный синтаксис и полный перечень параметров дают `pgw --help` и
`pgw <команда> --help`. Проверяемый сквозной сценарий с флагами, кодами
завершения и различиями Bash и PowerShell —
[`examples/06_cli_round_trip.md`](examples/06_cli_round_trip.md). Ротация
ключа — [`examples/05_key_rotation.md`](examples/05_key_rotation.md). Коды
возврата, режимы восстановления и классификация токенов —
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Библиотечное использование

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

## Примеры

Воспроизводимые сценарии и требования к их запуску собраны в
[`examples/README.md`](examples/README.md): минимальный round-trip, полный
цикл с несколькими типами сущностей, блокировка секрета, недоверенный ответ
модели, ротация ключа и сквозной сценарий CLI.

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
| [`docs/agent-contracts.md`](docs/agent-contracts.md) | Роли, trust boundaries и JSON-контракты executor/controller |
| [`docs/ADR-165-agent-orchestrator.md`](docs/ADR-165-agent-orchestrator.md) | Изолированный budgeted executor-controller loop и fail-closed границы |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Журнал архитектурных решений (ADR) |
| [`examples/README.md`](examples/README.md) | Индекс воспроизводимых примеров |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Definition of Done, quality gate, модель ветвления |

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
