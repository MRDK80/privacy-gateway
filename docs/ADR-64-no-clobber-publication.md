# ADR-64: no-clobber публикация артефактов prepare при overwrite=False

- Status: Accepted (research GO, owner decision 2026-08-29)
- Issue: #64 `integrity: no-clobber публикация артефактов prepare при overwrite=False (устранение TOCTOU)`
- Base commit: `3f40d67c36f94e4819d47a6afa65904ca01913e8`
- Уточняет: ADR-45 (publication order / atomic manifest policy), ADR-48 (variant A, best-effort preflight)
- Не отменяет: ADR-45 publication order, ADR-48 path-entry semantics, поведение `overwrite=True`
- Область: research-решение. Production-код изменяется отдельным causal PR после RED-доказательства.

## 1. Контекст

После #63 (PR #78) единый preflight в `prepare_pipeline()` проверяет `prompt.txt`, `route.json`,
`manifest.json` через `os.path.lexists()` и при занятом имени возвращает `BLOCKED` до первого
writer call. Публикация каждого артефакта выполняется через temp-файл в целевом каталоге и
завершается `os.replace()`.

Сохраняется TOCTOU-окно:

1. preflight видит имя свободным;
2. конкурентный процесс создаёт объект по этому имени;
3. writer завершает публикацию через `os.replace()`;
4. чужой объект молча заменяется, несмотря на `overwrite=False`.

Повторная проверка непосредственно перед `os.replace()` окно не закрывает: конкурент может
создать объект между проверкой и вызовом. POSIX `rename()` по спецификации атомарно заменяет
существующий `newpath`, поэтому `os.replace()` принципиально не может быть no-clobber
операцией на Unix.

Текущая гарантия — best-effort preflight, а не strict concurrent no-clobber.

## 2. Решение

Вводится единый publication primitive с платформенным разделением. Логика no-clobber
размещается в общем хелпере и используется обоими writer paths
(`pipeline._write_atomic()` и `manifest.save_manifest()`).

```text
overwrite=False:
  POSIX   : записать temp в целевой каталог -> close ->
            os.link(temp, target) -> os.unlink(temp)   # best-effort cleanup
  Windows : записать temp в целевой каталог -> close ->
            os.rename(temp, target)                    # НЕ os.replace

overwrite=True:
  без изменений: записать temp -> close -> os.replace(temp, target)
```

Порядок публикации ADR-45 сохраняется без изменений:

```text
manifest.json -> manifest_sha256 -> prompt.txt -> route.json
```

`overwrite` передаётся явным аргументом до writer boundary; writer не выводит режим
из внешнего состояния.

### 2.1 Почему POSIX `os.link`

POSIX определяет `link()` как атомарное создание новой directory entry для существующего файла
и требует `EEXIST`, если `path2` разрешается в существующую directory entry или является
symbolic link. Это даёт identity-safe создание имени: конкурентный объект, выигравший race,
не заменяется, а операция отказывает. Target появляется в файловой системе только после того,
как temp полностью записан и закрыт, поэтому atomic visibility одного файла сохраняется.

### 2.2 Почему Windows `os.rename`, а не `os.link`

На Windows `os.rename()` уже является no-clobber операцией: при существующем `dst` всегда
возбуждается `FileExistsError`. Дополнительный native API не требуется.

`os.link()` на Windows реализован через `CreateHardLink`, который официально поддерживается
только на NTFS и только для файлов, не каталогов, причём все ссылки обязаны находиться на одном
томе. Практические следствия:

- FAT32 не поддерживает hard links вообще;
- ReFS получил поддержку hard links только начиная с версии 3.5 и только на свежеформатированных
  томах; ReFS 3.4 является форматом по умолчанию для Windows 10 v1803 и Windows Server 2019;
- часть сетевых путей поддержки не даёт.

Единый `os.link` на обеих платформах при обязательном fail-closed поведении означал бы регресс
функциональности: `prepare` с `overwrite=False` начал бы отказывать там, где сегодня работает.
Платформенное разделение стоит одну ветвь в коде и сохраняет охват файловых систем.

Та же асимметрия используется в независимой эталонной реализации `python-atomicwrites`:
на POSIX — `os.link()` + `os.unlink()`, на Windows — перемещение без флага
`MOVEFILE_REPLACE_EXISTING`. Отличие в нашу пользу: эталон идёт в Win32 через `ctypes`,
а `os.rename()` даёт ту же семантику средствами стандартной библиотеки, без новых
runtime dependencies.

### 2.3 Fail closed

При `overwrite=False` fallback на `os.replace()` запрещён. Если no-clobber primitive недоступен
(нет поддержки hard links, отказ прав, sharing violation), операция завершается явной ошибкой.
Молчаливый откат уничтожил бы гарантию, оставив её недоказуемой для вызывающего кода.

## 3. Граница гарантии

Гарантируется узко: **операция не заменяет path entry, существующий в момент атомарного
создания целевого имени.**

Не гарантируется:

- защита target от последующего удаления или замены независимым процессом;
- crash durability и `fsync` (в том числе directory fsync — сознательно не добавляется,
  durability вне scope #64);
- транзакционность всего artifact set;
- cross-process locking;
- защита от процесса, работающего с тем же каталогом с `overwrite=True`.

Важное свойство контракта: при `overwrite=False` отказ по коллизии возникает **после** того,
как temp уже полностью записан. Temp удаляется best-effort; отказ `unlink` не отменяет
состоявшуюся публикацию и не маскирует первичную ошибку.

## 4. Error contract

| Условие | Исключение primitive | Публичный контракт |
|---|---|---|
| Целевое имя занято (файл, каталог, symlink, broken symlink, junction) | `FileExistsError` | `PipelineResult(BLOCKED)`, CLI prefix `BLOCKED: `, exit code 3 |
| ФС не поддерживает hard links (POSIX, `EPERM`) | `OSError`/`PermissionError` с `errno.EPERM` | Не BLOCKED. Отдельная ошибка окружения с явным сообщением о неподдерживаемой файловой системе |
| Windows sharing violation / antivirus-скан temp | `PermissionError` | Не BLOCKED. Отдельная ошибка окружения, отличимая от коллизии имени |
| Прочие сбои writer | `OSError` | Существующее поведение сохраняется |

Ключевое требование: `EPERM` и `PermissionError` **не** мапятся в `BLOCKED`. BLOCKED означает
именно конфликт имени; смешение маскировало бы отказ механизма под нормальную коллизию.
Сырой `OSError` наружу не выпускается — сообщение должно называть причину.

## 5. Semantics позднего race (partial artifact set)

Коллизия может возникнуть на втором или третьем артефакте, когда предыдущие уже опубликованы.
Принятое поведение:

- чужой объект не удаляется и не заменяется;
- ранее опубликованные собственные артефакты **не** откатываются: доказать владение без
  транзакционной/ownership-подсистемы невозможно, а такая подсистема вне scope;
- `route.json` не публикуется, набор считается незавершённым;
- `route.json` остаётся завершающим маркером artifact set (ADR-45).

Это признаётся осознанным ухудшением относительно текущего поведения, где BLOCKED
возвращался до первого writer call. Ухудшение принимается, поскольку альтернатива —
транзакция всего набора — исключена из scope #64.

## 6. Отклонённые альтернативы

| Вариант | Причина отклонения |
|---|---|
| A. Прямая запись `O_CREAT \| O_EXCL` в target | Имя резервируется атомарно, но target становится видимым до окончания записи; ломает atomic visibility, критично для `manifest.json` и завершающего `route.json` |
| B. Exclusive placeholder + `os.replace` | Конкурент может удалить или заменить placeholder между claim и replace; `os.replace()` перезапишет объект, находящийся по имени в момент вызова. Identity-binding отсутствует |
| D. `os.link` с fallback на `os.replace` | Молчаливая потеря гарантии при `overwrite=False` |
| E. `renameat2(RENAME_NOREPLACE)` / native Win32 | Нет stdlib API; поддержка зависит от ядра и ФС (ext4 с 3.15, btrfs/tmpfs/cifs с 3.17, xfs с 4.0, остальные с 4.9); требует `ctypes` или новой зависимости |
| F. Единый `os.link` на обеих платформах | Fail closed на FAT32, ReFS 3.4 и части SMB — регресс функциональности на Windows |
| G. Сохранение ADR-48 variant A | Оставляет воспроизводимый TOCTOU при `overwrite=False` |

## 7. Обязательные тесты

Без перечисленных доказательств решение считается непринятым.

- Детерминированный RED-тест race на `manifest.json`: foreign target создаётся после preflight,
  на writer boundary; assertion — байты чужого объекта не изменились.
- То же для `prompt.txt` и для `route.json`.
- Детерминированный late-race: коллизия на `prompt.txt` при уже опубликованном `manifest.json`;
  фиксирует раздел 5 как ожидаемое поведение.
- POSIX: `os.link` при destination = broken symlink → `FileExistsError`, запись «сквозь» ссылку
  недопустима.
- Windows: `os.rename` при существующем каталоге по имени target → отказ, не replace.
- Windows: `os.rename` при существующем junction по имени target → отказ, не replace.
- POSIX: `EPERM` на ФС без hard links → ошибка окружения, не BLOCKED.
- Windows: `PermissionError` (sharing/AV) → ошибка окружения, не BLOCKED.
- Cleanup: отказ `os.link`/`os.rename` не оставляет temp; отказ `unlink(temp)` после успешной
  публикации не откатывает её.
- Regression: `overwrite=True` сохраняет replace semantics и порядок ADR-45.
- Тесты не используют `sleep` и не зависят от планировщика; skip на Ubuntu и Windows
  не допускается, кроме платформенно-специфичных кейсов (junction — только Windows).

## 8. Требования к верификации

- Full local gate: `pytest -q`, `ruff check .`, `mypy .`, `pre-commit run --all-files`.
- Точные CI-джобы: Ubuntu latest / Python 3.11, Ubuntu latest / 3.12,
  Windows latest / 3.11, Windows latest / 3.12, pre-commit.
- RED → GREEN: сначала падающие тесты на текущем `os.replace()` path, затем минимальное
  production-изменение.

## 9. Вне scope

Транзакция всего набора артефактов, staging directory, rollback как подсистема, crash durability
и `fsync`, cross-process locking, изменение route/manifest formats, `ROUTE_FORMAT_VERSION`,
`CONTEXT_FORMAT_VERSION`, переработка publication order ADR-45, изменение `overwrite=True`,
`ctypes`/pywin32/сторонние atomic-write пакеты, issues #81 и #82, version bump, tag, Release.
