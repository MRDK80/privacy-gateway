# 06_cli_round_trip.md

Сквозной сценарий CLI: подготовка запроса, локальная имитация внешнего
ответа, восстановление значений и очистка артефактов. Пример показывает
только фактически существующие команды и флаги `pgw`.

Пример без исполняемого скрипта: сценарий состоит из вызовов CLI, а
`pgw key create` изменяет системный keyring текущей учётной записи.
Автоматическая проверка сценария выполняется в
`tests/test_examples_cli_round_trip.py` на изолированном keystore.

## Требования безопасности

- Внешний провайдер в примере не вызывается: ответ имитируется локальным
  детерминированным преобразованием защищённого текста.
- За границу доверия передаётся только `prompt.txt`.
- `route.json` и `manifest.json` остаются локально и никуда не отправляются.
- Ключевой материал не печатается ни одной командой.
- Все рабочие файлы создаются в отдельном каталоге и удаляются на шаге
  очистки.

## Подготовка окружения

```bash
pip install -e ".[dev]"
pgw key status
```

Если активного ключа нет, `pgw key status` завершается кодом `3` и печатает
в stderr `Ключ не найден. Запустите 'pgw key create'.`. Создание ключа:

```bash
pgw key create
```

Успех даёт код `0` и stdout `Ключ успешно создан.`. Повторный
`pgw key create` без `--force` завершается кодом `3`: существующий ключ
не перезаписывается молча.

## Шаг 1. Синтетический запрос

Bash:

```bash
mkdir -p ./pgw_demo
cat > ./pgw_demo/request.txt <<'EOF'
Клиент user@example.com сообщил сбой на узле 192.0.2.10.
Контактный телефон +7 900 000-00-00.
EOF
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path .\pgw_demo | Out-Null
@'
Клиент user@example.com сообщил сбой на узле 192.0.2.10.
Контактный телефон +7 900 000-00-00.
'@ | Set-Content -Encoding utf8 .\pgw_demo\request.txt
```

Значения синтетические: домен `example.com`, адрес из документационного
диапазона `192.0.2.0/24` и телефон из непубличного префикса.

## Шаг 2. Подготовка запроса

```bash
pgw prepare ./pgw_demo/request.txt --out ./pgw_demo/work
```

Код `0`, stdout одной строкой:

```text
OK: <prompt.txt> / <route.json> / <manifest.json>
```

В каталоге `./pgw_demo/work` появляются три файла: `prompt.txt`,
`route.json` и `manifest.json`. Подкаталоги не создаются. Исходные
значения отсутствуют в `prompt.txt` и в `route.json`; значения хранятся
только в зашифрованных полях `manifest.json`.

Повторный `pgw prepare` в тот же каталог без `--overwrite` завершается
кодом `3` и печатает в stderr строку с префиксом `BLOCKED:`; существующие
артефакты не перезаписываются.

## Шаг 3. Имитация внешнего ответа

За границу доверия уходит только `prompt.txt`. Вместо внешнего провайдера
применяется детерминированное преобразование: к защищённому тексту
добавляется строка статуса, токены не изменяются.

Bash:

```bash
cp ./pgw_demo/work/prompt.txt ./pgw_demo/reply.txt
printf '\nСтатус: заявка принята.\n' >> ./pgw_demo/reply.txt
```

PowerShell:

```powershell
Copy-Item .\pgw_demo\work\prompt.txt .\pgw_demo\reply.txt
Add-Content -Encoding utf8 .\pgw_demo\reply.txt "`nСтатус: заявка принята."
```

## Шаг 4. Восстановление

```bash
pgw restore ./pgw_demo/reply.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/restored.txt
```

Код `0`. В stdout печатается `OK: <путь к restored.txt>`. Отчёт по токенам
идёт в stderr и начинается со строки вида `Восстановлено: N/N токенов`.
Отчёт содержит только имена токенов и счётчики; исходные значения в него
не попадают.

Файл `./pgw_demo/restored.txt` содержит исходные значения и добавленную
строку статуса. Без `--out` восстановленный текст печатается в stdout без
добавления перевода строки.

Путь к `manifest.json` по умолчанию разрешается относительно каталога
`route.json`. Явный путь задаётся флагом `--manifest`.

## Шаг 5. Fail-closed сценарий

Строгий режим включён по умолчанию. Ответ с токеном, которого нет в
манифесте, отклоняется целиком.

Bash:

```bash
cp ./pgw_demo/work/prompt.txt ./pgw_demo/bad_reply.txt
printf '\nСвяжитесь с [EMAIL_99].\n' >> ./pgw_demo/bad_reply.txt
pgw restore ./pgw_demo/bad_reply.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/bad_restored.txt
```

Код `5`. В stderr печатается сообщение с префиксом
`Строгий отказ по токенам:`. Файл `./pgw_demo/bad_restored.txt` не
создаётся: частичный результат не публикуется. Исходные значения в stderr
не раскрываются.

Флаг `--lenient` переводит неизвестные и искажённые токены в
предупреждения и возвращает код `0`. Использовать его следует осознанно:
строгий режим — единственный, который гарантирует отказ вместо
частичного восстановления.

## Шаг 6. Очистка

Bash:

```bash
rm -rf ./pgw_demo
```

PowerShell:

```powershell
Remove-Item -Recurse -Force .\pgw_demo
```

Удаляется весь рабочий каталог, включая `manifest.json`. Ключ в keyring
командой очистки не затрагивается: CLI-команды удаления ключа не
существует. Управление ключом ограничено `pgw key create`,
`pgw key status` и `pgw key rotate`.

## Exit/result contract

| Шаг | Команда | Код | Наблюдаемый результат |
| --- | --- | --- | --- |
| Проверка ключа | `pgw key status` | `0` | stdout: ключ присутствует |
| Проверка ключа | `pgw key status` | `3` | stderr: ключ не найден |
| Создание ключа | `pgw key create` | `0` | stdout: ключ создан |
| Создание ключа | `pgw key create` повторно | `3` | stderr: ключ уже существует |
| Подготовка | `pgw prepare` | `0` | stdout: `OK: ... / ... / ...`, три артефакта |
| Подготовка | `pgw prepare` без `--overwrite` | `3` | stderr: префикс `BLOCKED:` |
| Восстановление | `pgw restore --out` | `0` | stdout: `OK: <путь>`, отчёт в stderr |
| Восстановление | неизвестный токен, строгий режим | `5` | stderr: строгий отказ, файла нет |
| Любая команда | ключ недоступен | `4` | stderr: ошибка keystore |

Ошибка использования argparse даёт код `3`, а не `2`: код `2`
зарезервирован для статуса `PENDING`.

## Проверяемые инварианты

- `prompt.txt` и `route.json` не содержат исходных значений.
- Отчёт восстановления не содержит исходных значений.
- Round-trip точен: восстановленный текст содержит исходные значения
  запроса.
- При строгом отказе файл результата не создаётся.
- Рабочий каталог полностью удаляется на шаге очистки.
- Сценарий не обращается к сети.

Автоматическая проверка: `tests/test_examples_cli_round_trip.py`. Тест
запускает те же команды отдельными процессами на Linux и Windows,
подменяя keystore в изолированном `PYTHONPATH`, поэтому системный keyring
и домашний каталог пользователя не используются.


## Автоматизация через JSON

```bash
pgw --json prepare ./pgw_demo/request.txt --out ./pgw_demo/work
pgw --json key status
pgw --json restore ./pgw_demo/response.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/restored.txt
```

`restore --json` требует `--out`: восстановленный чувствительный текст попадает
только в указанный файл, а stdout содержит один служебный JSON object. Передавать
`route.json`, `manifest.json` или восстановленный файл провайдеру нельзя.
Контракт описан в
[`docs/ADR-150-cli-json-contract.md`](../docs/ADR-150-cli-json-contract.md).
## Машиночитаемый каталог для агента

До первого операционного вызова агент может получить фактический CLI-контракт
без парсинга help:

```bash
pgw --describe
```

Вывод — один JSON object: команды, параметры с признаком обязательности,
машинные типы, допустимые значения, форматы вывода, side effects, process exit
codes и machine error codes.

Безопасный сценарий использования — выбрать команды, поддерживающие
JSON-режим, до запуска операционного вызова:

```bash
pgw --describe > ./pgw_demo/catalog.json
python - <<'PY'
import json

with open("./pgw_demo/catalog.json", encoding="utf-8") as handle:
    catalog = json.load(handle)

print(catalog["schema_version"], catalog["kind"])
for command in catalog["commands"]:
    if "json" in command["output_formats"]:
        print(command["id"], command["json_mode_requires"])
PY
```

`pgw --describe` не читает `request.txt`, `route.json` и `manifest.json`, не
обращается к keyring и не создаёт файлов, поэтому его можно выполнять до
`pgw key create`. Каталог не содержит ключевого материала, исходных значений,
локальных абсолютных путей и значений окружения. Повторный вызов на той же
версии кода даёт побайтово одинаковый результат.

Каталог отражает фактическое состояние: `detect` работает только в
человекочитаемом режиме, а `restore` в JSON-режиме требует `--out`. Контракт
каталога описан в
[`docs/ADR-151-cli-introspection.md`](../docs/ADR-151-cli-introspection.md).
