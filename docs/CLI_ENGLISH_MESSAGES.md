# Approved CLI text inventory — #122

Source locations refer to pre-migration commit
`11e781f364ff00da572cab5d548ecf45a2de322d`; they are historical inventory, not
current line pointers. [ADR-122](ADR-122-english-cli.md) defines compatibility.

Fragments retain existing interpolated expressions and punctuation. Values such
as token names, paths, user inputs and upstream exception details are not newly
added or translated. Generated argparse English text remains implementation-owned.

| Source | Existing text / fragment | Approved English |
| --- | --- | --- |
| src/privacy_gateway/cli.py:146 | `Непредвиденная ошибка` | `Unexpected error` |
| src/privacy_gateway/cli.py:753 | `ФАЙЛ` | `FILE` |
| src/privacy_gateway/cli.py:754 | `КАТАЛОГ` | `DIRECTORY` |
| src/privacy_gateway/cli.py:168 | `Внутренняя ошибка.` | `Internal error.` |
| src/privacy_gateway/cli.py:180 | `Ошибка чтения` | `Read error` |
| src/privacy_gateway/cli.py:190 | `Ошибка чтения ответа LLM` | `Cannot read LLM response` |
| src/privacy_gateway/cli.py:200 | `Ошибка конфигурации` | `Configuration error` |
| src/privacy_gateway/cli.py:210 | `Ошибка keystore` | `Key-store error` |
| src/privacy_gateway/cli.py:225 | `Ошибка` | `Error` |
| src/privacy_gateway/cli.py:231 | `Ошибка записи` | `Write error` |
| src/privacy_gateway/cli.py:653 | `Ключ успешно создан.` | `Key created successfully.` |
| src/privacy_gateway/cli.py:720 | `Ротация выполнена. Новый ключ активен. Старый ключ сохранён для чтения манифестов, созданных до ротации.` | `Rotation completed. The new key is active. The previous key is retained to read manifests from the previous generation.` |
| src/privacy_gateway/cli.py:789 | `Печатает машиночитаемый каталог CLI-контракта.` | `Print the machine-readable CLI contract catalog.` |
| src/privacy_gateway/cli.py:795 | `Включает JSON-режим поддерживаемых операционных команд.` | `Enable JSON output for supported operational commands.` |
| src/privacy_gateway/cli.py:182 | `Не удалось прочитать входные данные.` | `Could not read input data.` |
| src/privacy_gateway/cli.py:192 | `Не удалось прочитать входные данные.` | `Could not read input data.` |
| src/privacy_gateway/cli.py:202 | `Недопустимая конфигурация.` | `Invalid configuration.` |
| src/privacy_gateway/cli.py:214 | `Операция с хранилищем ключей не выполнена.` | `Key-store operation failed.` |
| src/privacy_gateway/cli.py:226 | `Не удалось записать результат.` | `Could not write the result.` |
| src/privacy_gateway/cli.py:232 | `Не удалось записать результат.` | `Could not write the result.` |
| src/privacy_gateway/cli.py:240 | `Privacy Gateway — безопасная подготовка текста для LLM.` | `Privacy Gateway: local text preparation for an LLM.` |
| src/privacy_gateway/cli.py:247 | `Проверка базовой установки.` | `Check the base installation.` |
| src/privacy_gateway/cli.py:252 | `Диагностика: найти сущности без шифрования.` | `Diagnostic entity detection without encryption.` |
| src/privacy_gateway/cli.py:256 | `ФАЙЛ` | `FILE` |
| src/privacy_gateway/cli.py:257 | `Путь к файлу или '-' для stdin.` | `File path, or '-' for stdin.` |
| src/privacy_gateway/cli.py:263 | `Кодировка входного файла (utf-8, cp1251 и др.).` | `Input encoding (utf-8, cp1251, etc.).` |
| src/privacy_gateway/cli.py:269 | `Путь к entities.yaml детектора.` | `Path to the detector entities.yaml configuration.` |
| src/privacy_gateway/cli.py:275 | `Подготовка: токенизация, шифрование, запись артефактов.` | `Prepare text: tokenize, encrypt and write artifacts.` |
| src/privacy_gateway/cli.py:279 | `ФАЙЛ` | `FILE` |
| src/privacy_gateway/cli.py:280 | `Путь к файлу или '-' для stdin.` | `File path, or '-' for stdin.` |
| src/privacy_gateway/cli.py:284 | `КАТАЛОГ` | `DIRECTORY` |
| src/privacy_gateway/cli.py:286 | `Каталог для артефактов (prompt.txt, route.json, manifest.json).` | `Artifact directory (prompt.txt, route.json, manifest.json).` |
| src/privacy_gateway/cli.py:292 | `Путь к YAML-конфигу маршрутизации.` | `Path to the routing YAML configuration.` |
| src/privacy_gateway/cli.py:299 | `Путь к entities.yaml детектора. По умолчанию: config.example/entities.yaml.` | `Path to detector entities.yaml. Default: config.example/entities.yaml.` |
| src/privacy_gateway/cli.py:307 | `Кодировка входного файла.` | `Input file encoding.` |
| src/privacy_gateway/cli.py:313 | `Перезаписывать существующие артефакты.` | `Overwrite existing artifacts.` |
| src/privacy_gateway/cli.py:319 | `Восстановление исходных значений из ответа LLM.` | `Restore original values in an LLM response.` |
| src/privacy_gateway/cli.py:323 | `ФАЙЛ` | `FILE` |
| src/privacy_gateway/cli.py:324 | `Путь к файлу с ответом LLM или '-' для stdin.` | `LLM response file path, or '-' for stdin.` |
| src/privacy_gateway/cli.py:330 | `Путь к route.json от соответствующего запуска prepare.` | `Path to route.json from the corresponding prepare run.` |
| src/privacy_gateway/cli.py:334 | `ФАЙЛ` | `FILE` |
| src/privacy_gateway/cli.py:336 | `Путь к файлу результата. Если не указан — вывод в stdout.` | `Result file path. If omitted, write to stdout.` |
| src/privacy_gateway/cli.py:342 | `Перезаписывать существующий файл результата.` | `Overwrite an existing result file.` |
| src/privacy_gateway/cli.py:349 | `Явный путь к manifest.json. По умолчанию разрешается относительно каталога route.json (ADR-15).` | `Explicit manifest.json path. The default is resolved relative to the route.json directory (ADR-15).` |
| src/privacy_gateway/cli.py:358 | `Мягкий режим: неизвестные и искажённые токены дают предупреждение, а не ошибку. По умолчанию применяется строгий режим (ADR-16).` | `Lenient mode: unknown or malformed tokens produce warnings instead of errors. Strict mode is the default (ADR-16).` |
| src/privacy_gateway/cli.py:366 | `Управление Fernet-ключом в keyring.` | `Manage the Fernet key in the system keyring.` |
| src/privacy_gateway/cli.py:373 | `Создать новый Fernet-ключ в keyring.` | `Create a new Fernet key in the system keyring.` |
| src/privacy_gateway/cli.py:380 | `Перезаписать существующий ключ. ВНИМАНИЕ: все ранее созданные манифесты станут нечитаемыми. Для безопасной ротации используйте 'pgw key rotate'.` | `Overwrite the existing key. WARNING: all previously created manifests will become unreadable. To rotate instead, use 'pgw key rotate'.` |
| src/privacy_gateway/cli.py:389 | `Проверить наличие ключа без вывода его значения.` | `Check key presence without printing its value.` |
| src/privacy_gateway/cli.py:396 | `Ротация ключа: новый становится активным, старый остаётся для чтения манифестов.` | `Rotate the key: activate a new key and retain the previous one for manifest reads.` |
| src/privacy_gateway/cli.py:528 | `Ошибка конфигурации` | `Configuration error` |
| src/privacy_gateway/cli.py:536 | `Ошибка keystore` | `Key-store error` |
| src/privacy_gateway/cli.py:546 | `Строгий отказ по токенам` | `Strict token rejection` |
| src/privacy_gateway/cli.py:556 | `Ошибка восстановления` | `Restore error` |
| src/privacy_gateway/cli.py:578 | `Восстановлено: ` | `Restored: ` |
| src/privacy_gateway/cli.py:579 | ` токенов` | ` tokens` |
| src/privacy_gateway/cli.py:627 | `Ошибка keystore` | `Key-store error` |
| src/privacy_gateway/cli.py:665 | `Ошибка keystore` | `Key-store error` |
| src/privacy_gateway/cli.py:680 | `Ключ присутствует в keyring.` | `Key is present in the system keyring.` |
| src/privacy_gateway/cli.py:683 | `Ключ не найден.` | `Key not found.` |
| src/privacy_gateway/cli.py:685 | `Ключ не найден. Запустите 'pgw key create'.` | `Key not found. Run 'pgw key create'.` |
| src/privacy_gateway/cli.py:699 | `Ключ не найден. Запустите 'pgw key create' сначала. Детали` | `Key not found. Run 'pgw key create' first. Details` |
| src/privacy_gateway/cli.py:705 | `Ошибка keystore` | `Key-store error` |
| src/privacy_gateway/cli.py:1092 | `нераспознанная команда: ` | `unrecognized command: ` |
| src/privacy_gateway/cli.py:1106 | `JSON не поддерживается для ` | `JSON is not supported for ` |
| src/privacy_gateway/cli.py:1118 | `Команда не поддерживает JSON-режим.` | `This command does not support JSON output.` |
| src/privacy_gateway/cli.py:1126 | `JSON-режим restore требует --out.` | `JSON restore requires --out.` |
| src/privacy_gateway/cli.py:1158 | `Внутренняя ошибка.` | `Internal error.` |
| src/privacy_gateway/cli.py:106 | `незарегистрированный machine error code: ` | `unregistered machine error code: ` |
| src/privacy_gateway/cli.py:497 | `Требуется ручное подтверждение.` | `Manual approval is required.` |
| src/privacy_gateway/cli.py:503 | `Обработка заблокирована политикой.` | `Processing was blocked by policy.` |
| src/privacy_gateway/cli.py:530 | `Недопустимая конфигурация.` | `Invalid configuration.` |
| src/privacy_gateway/cli.py:540 | `Операция с хранилищем ключей не выполнена.` | `Key-store operation failed.` |
| src/privacy_gateway/cli.py:550 | `Строгое восстановление отклонено.` | `Strict restoration was rejected.` |
| src/privacy_gateway/cli.py:558 | `Восстановление не выполнено.` | `Restoration failed.` |
| src/privacy_gateway/cli.py:574 | `ПРЕДУПРЕЖДЕНИЕ: ` | `WARNING: ` |
| src/privacy_gateway/cli.py:584 | `  Не найдено в ответе: ` | `  Missing from response: ` |
| src/privacy_gateway/cli.py:589 | `  Неизвестных токенов: ` | `  Unknown tokens: ` |
| src/privacy_gateway/cli.py:594 | `  Искажённых кандидатов: ` | `  Malformed candidates: ` |
| src/privacy_gateway/cli.py:599 | `  Дублированных токенов: ` | `  Duplicate tokens: ` |
| src/privacy_gateway/cli.py:631 | `Операция с хранилищем ключей не выполнена.` | `Key-store operation failed.` |
| src/privacy_gateway/cli.py:640 | `Ключ уже существует.` | `Key already exists.` |
| src/privacy_gateway/cli.py:644 | `Ключ уже существует. Используйте --force для перезаписи (внимание: все существующие манифесты станут нечитаемыми). Для безопасной ротации используйте 'pgw key rotate'.` | `Key already exists. Use --force to overwrite it (WARNING: all existing manifests will become unreadable). To rotate instead, use 'pgw key rotate'.` |
| src/privacy_gateway/cli.py:669 | `Операция с хранилищем ключей не выполнена.` | `Key-store operation failed.` |
| src/privacy_gateway/cli.py:700 | `Ключ не найден.` | `Key not found.` |
| src/privacy_gateway/cli.py:709 | `Операция с хранилищем ключей не выполнена.` | `Key-store operation failed.` |
| src/privacy_gateway/cli.py:1043 | ` не принимает дополнительные аргументы.` | ` does not accept additional arguments.` |
| src/privacy_gateway/cli.py:1091 | `нераспознанная key-подкоманда: ` | `unrecognized key subcommand: ` |
| src/privacy_gateway/cli.py:937 | `нет машинного типа для параметра ` | `no machine type for parameter ` |
| src/privacy_gateway/cli.py:1143 | `Внутренняя ошибка.` | `Internal error.` |
| src/privacy_gateway/cli.py:1069 | `Недопустимые аргументы командной строки.` | `Invalid command-line arguments.` |
| src/privacy_gateway/bootstrap.py:25 | `Команда не поддерживает JSON-режим.` | `This command does not support JSON output.` |
| src/privacy_gateway/doctor.py:198 | `Проверить выбранную конфигурацию детектора.` | `Check the selected detector configuration.` |
| src/privacy_gateway/restore.py:185 | `Не удалось загрузить манифест ` | `Could not load manifest ` |
| src/privacy_gateway/restore.py:185 | `: ни один из ` | `: none of the ` |
| src/privacy_gateway/restore.py:186 | ` ключей не подошёл. Детали: ` | ` keys matched. Details: ` |
| src/privacy_gateway/restore.py:271 | `Токен отсутствует в ответе LLM: ` | `Token missing from LLM response: ` |
| src/privacy_gateway/restore.py:280 | `Строгий режим: в ответе LLM обнаружены недопустимые токены. ` | `Strict mode: the LLM response contains unacceptable tokens. ` |
| src/privacy_gateway/restore.py:307 | `Файл уже существует: ` | `File already exists: ` |
| src/privacy_gateway/restore.py:307 | `. Используйте флаг --overwrite для перезаписи.` | `. Use --overwrite to replace it.` |
| src/privacy_gateway/restore.py:233 | `Ключ Fernet не найден в keyring. Запустите 'pgw key create' для создания ключа. Детали: ` | `Fernet key not found in the system keyring. Run 'pgw key create' to create one. Details: ` |
| src/privacy_gateway/restore.py:276 | `Неизвестные токены: ` | `Unknown tokens: ` |
| src/privacy_gateway/restore.py:278 | `Искажённые кандидаты на токены: ` | `Malformed token candidates: ` |
| src/privacy_gateway/restore.py:287 | `Неизвестный токен (оставлен как есть): [` | `Unknown token (left unchanged): [` |
| src/privacy_gateway/restore.py:291 | `Искажённый кандидат (оставлен как есть): [` | `Malformed candidate (left unchanged): [` |
| src/privacy_gateway/restore.py:340 | `Не удалось записать результат в ` | `Could not write the result to ` |
| src/privacy_gateway/restore.py:251 | `Не удалось расшифровать запись манифеста для токена ` | `Could not decrypt the manifest record for token ` |
| src/privacy_gateway/restore.py:252 | `. Детали: ` | `. Details: ` |
| src/privacy_gateway/keystore.py:150 | `Активный ключ не найден. Запустите 'pgw key create'.` | `Active key not found. Run 'pgw key create'.` |
| src/privacy_gateway/keystore.py:169 | `Активный ключ не найден. Запустите 'pgw key create'.` | `Active key not found. Run 'pgw key create'.` |
| src/privacy_gateway/keystore.py:199 | `Ключ уже существует. Используйте force=True для перезаписи.` | `Key already exists. Use force=True to overwrite it.` |
| src/privacy_gateway/keystore.py:247 | `Ротация не подтверждена: активный ключ не соответствует новому.` | `Rotation not verified: the active key does not match the new key.` |
