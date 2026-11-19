# Проверка опубликованного пакета (#116)

Эта процедура применяется после отдельно разрешённого production-релиза.
Подготовка tooling не выполняет публикацию: порядок релиза и ограничения
описаны в [RELEASE.md](RELEASE.md) и [ADR-114](ADR-114-trusted-publishing.md).
Ни зелёный исходный CI, ни локальный fixture не доказывают наличие версии на PyPI.

## До запуска

- Roadmap влит в main с отдельным owner approval; actual post-merge CI зелёный.
- В отдельном release-действии подтверждены exact source/policy SHA, новая версия,
  неизменяемый tag, protected environment и PyPI identity. После этого выполнены
  настоящий dry-run, разрешённая публикация и первичная сверка её результата.
- Скачан verified artifact соответствующего run/attempt. Он содержит `manifest.json`,
  `SHA256SUMS` и ровно два файла в `dist/`. Его mode должен быть publish, не dry-run.
- Digest SHA256SUMS взят **отдельно из primary summary verify job**, а не вычислен
  из того же локального bundle как собственное доказательство доверия. Summary
  также показывает source/policy SHA; сохраните run/job IDs и actual publish result.
- Checkout tooling соответствует проверенному policy SHA; выбраны Python 3.11/3.12
  и новая пустая временная папка вне checkout. Существующий результат не перезаписывается.

Сейчас подготовка отделена от production-релиза явным owner disposition
2026-11-19. GitHub tag/release v0.5.0 уже существуют на старом source SHA:
их нельзя перемещать. Номер следующего релиза и его операции требуют отдельного
решения. После ROADMAP DONE создаётся отдельная release issue с этими условиями;
в рамках #116 версия, tags, настройки и публикация не меняются.

## Linux

Задайте переменные `BUNDLE`, `VERSION`, `SOURCE_SHA`, `SUMS_DIGEST` и `SMOKE_ROOT`
проверенными значениями. `SMOKE_ROOT` должен быть новым каталогом вне checkout.
Для Python 3.11 замените выбранный интерпретатор соответственно.

```bash
python3.12 tools/verify_published_package.py \
  --bundle "$BUNDLE" --version "$VERSION" \
  --source-sha "$SOURCE_SHA" --sums-sha256 "$SUMS_DIGEST" \
  --temp-root "$SMOKE_ROOT"
```

## Windows PowerShell

`$Python` должен выбирать установленный Python 3.11/3.12. Задайте `$Bundle`,
`$Version`, `$SourceSha`, `$SumsDigest` и новый `$SmokeRoot` теми же проверенными
значениями. Активация venv и изменение ExecutionPolicy не требуются.

```powershell
$Python = 'python'
& $Python tools\verify_published_package.py `
  --bundle $Bundle --version $Version `
  --source-sha $SourceSha --sums-sha256 $SumsDigest `
  --temp-root $SmokeRoot
if ($LASTEXITCODE -ne 0) { throw 'Post-publication verification did not pass' }
```

## Что проверяется

Клиент запрашивает exact-version JSON у PyPI, сверяет имя/версию, полный набор
filenames, sizes, SHA256 и отсутствие yanked status с доверенным bundle. Файлы
получаются по HTTPS только с files.pythonhosted.org, без redirects/credentials,
и проверяются до записи/установки. HTTP 404, несовпадение или отсутствующий файл
останавливают процедуру до installer. Установки по имени package как fallback нет.

Wheel и sdist устанавливаются из проверенных локальных файлов в два новых venv.
Pip работает без cache, с canonical index и отключёнными user/global/site config
через PIP_CONFIG_FILE=os.devnull; inherited overrides удаляются. Это не lock-файл
всего dependency graph и не обещание его побитовой воспроизводимости.

Вне checkout проверяются metadata/version, module location, реальный console
entrypoint, help/introspection, doctor и synthetic API round trip. До doctor
подставляется synthetic capability; выбор системного keyring и сеть во время
самого API/doctor smoke запрещены. Ключ существует только в памяти теста.
Команды installer могут читать package index для зависимостей.

## Evidence и отказ

Успех actual-режима — `POST_PUBLICATION: PASS` и `evidence.json`. Evidence содержит
source/policy SHA, PyPI version, filenames/hashes, release run/job IDs, platform,
команды с заменёнными временными путями, exit codes и результаты wheel/sdist checks.
Сам manifest не утверждает успешный upload: primary publish action/job result
сохраняется отдельно. Точные raw logs остаются локальными; POSIX файлы создаются
с mode 0600 внутри private directory, автоматически наружу не отправляются.

После запуска процесса, до ожидания результата, фиксируются phase/PID/timeout
и отсутствие доказанного progress;
после завершения — exit code. Один IO timeout не является hard deadline всей сети.
Ожидания команд ограничены; cleanup process group/tree — best effort. Непроверенный
Windows cleanup остаётся блокировкой, не поводом для автоматического повтора.

При отказе сохраните каталог и `blocked.json`/phase logs, сначала выясните исход.
Не перезаписывайте опубликованные артефакты и не перемещайте tag. Исправление
релиза — отдельное решение о новой версии; частичный upload сначала сверяется
read-only по filenames/hashes. Повтор smoke или публикации автоматически не запускается.

## Проверка tooling до публикации

Normal artifact gate дополнительно вызывает `--fixture-source` на локальных
проверенных wheel/sdist для native Linux/Windows. Provider metadata и IDs в этом
режиме вымышлены, target package не запрашивается на PyPI. Выполняются реальные
изолированные установки и synthetic checks; зависимости могут читаться из index.
Результат всегда `LOCAL_SIMULATION_PASS`, а не actual publication PASS.
Эти bundles нельзя использовать как authority настоящего релиза.
