# Подготовка релиза и Trusted Publishing

Политика определена в [ADR-114](ADR-114-trusted-publishing.md). Workflow
`.github/workflows/publish-pypi.yml` выполняется только вручную, по умолчанию
в режиме `dry-run`. Он не создаёт tags/releases, не меняет настройки и не
выполняет merge. Наличие файла workflow не является разрешением публикации.

## Разрешения и предварительные действия владельца

До production run нужны отдельные решения актуальных SHA и версии, main merge,
создание неизменяемого tag/release и фактическая публикация. Версия берётся
только из литерала package `__version__`, в этой задаче она не меняется.

После явного разрешения настроек владелец должен настроить:

1. GitHub environment `pypi`: required reviewer только User MRDK80 (id53867710),
   self-review разрешён для единственного владельца, **admin bypass запрещён**.
2. Custom deployment rule — только branch `main`, без tag rules и wildcard.
3. Trusted Publisher на PyPI: owner MRDK80, repo privacy-gateway,
   workflow publish-pypi.yml, environment pypi, project privacy-gateway.
   Сначала проверить владение именем; при создании проекта применяется
   [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

Настройки не выполняются автоматически этой реализацией. Если API не возвращает
проверяемое `can_admins_bypass: false`, reviewer или main-only rule, publish
блокируется. Missing и 403 не считаются значениями по умолчанию. Admission
поддерживает текущий public repository; при private/неизвестном enforcement
потребуется отдельная проверенная политика. Снимок API не является атомарным
lock настроек или защитой от последующей компрометации доверенного администратора.

## Непубликующая проверка

Inputs: `mode=dry-run`, точный `source_sha` из MRDK80/privacy-gateway и,
необязательно, планируемый `tag` вида v + canonical version. Dry-run собирает,
проверяет и сохраняет evidence без publish job/OIDC и не требует PyPI identity.
Отсутствие реального tag допускается только в dry-run и отмечается mode в evidence.

GitHub требует наличия workflow в default branch для ручного dispatch.
До отдельного main merge нельзя объявлять этот dispatch выполненным:
PR/static tests и локальная synthetic проверка helpers — другой уровень evidence.
Actual dry-run run ID и оба artifact IDs должны быть сохранены после такого run.

## Production последовательность

1. Подтвердить актуальные owner-разрешения, main/policy SHA, source SHA,
   package version, соответствующий неизменяемый tag и назначение PyPI project.
2. Запустить workflow из `main`, явно выбрать `mode=publish`, exact source SHA
   и существующий tag. Publish source должен быть предком actual main, а
   policy SHA — совпадать с actual main на момент verification. Если main
   изменился, запуск не продолжать по устаревшему admission.
3. Build job без OIDC собирает один передаваемый набор wheel/sdist и выполняет
   canonical artifact/install smoke. Verify работает на свежем runner:
   checkout policy из exact `github.workflow_sha`, source читается только как
   данные. Проверяются архивы, версия, license, module bytes, dependency metadata
   и entry points; код дистрибутива в verify не исполняется.
4. Verify повторяет ref/tag/main и environment API checks до допуска publish.
   Встроенная environment approval остаётся отдельным явным действием каждого run.
5. Publish не делает checkout и не запускает package code. Он скачивает только
   immutable verified artifact этого run, сверяет output digest SHA256SUMS и
   точные файлы, затем официальный SHA-pinned PyPA action публикует в PyPI.
   OIDC ограничен этим job; action создаёт стандартные attestations. Долгоживущий
   API token не используется. Approval workflow не разрешает вводить его fallback.
6. Проверить первичные job/action result и PyPI filenames/hashes, затем выполнить
   отдельный post-publication smoke #116. Зелёный dry-run не доказывает upload.

## Evidence и recovery

`manifest.json` фиксирует source/policy SHA, version/tag/mode, run/attempt,
verify job ID, filenames, sizes и SHA256. SHA256SUMS покрывает manifest и два
артефакта; его digest приходит отдельно из trusted verify outputs. Build и verify
передают immutable artifact IDs текущего run, а не names/другой run/latest.
Манифест рядом с файлами сам по себе не доказывает их доверенность.

Actual publish outcome — primary action/job result в run summary. Сохраните
manifest, SHA256SUMS, artifacts, run/job IDs и outcome локально: CI artifacts
хранятся 14 дней, а не бессрочно. Не сохраняйте OIDC/API tokens или credentials.

При timeout, неполном upload или неизвестном результате сначала read-only
сверьте PyPI и фактические hashes. Автоматических повторов публикации и
`skip-existing` нет. Не перемещайте tag и не перезаписывайте версию. Отдельное
решение владельца должно определить, допустим ли upload только недостающих
файлов того же набора или требуется новая версия. Отказ не маскируется как PASS.

Проверка archive/source соответствия не является сканером любых секретов в
произвольных данных и не обещает побитово воспроизводимую сборку всех зависимостей.
Проверки установки используют synthetic inputs; для пользовательских ключей
сохраняются [security boundary](SECURITY.md) и требования безопасного keyring.

## Подготовка и исполнение разделены

Owner disposition от 2026-11-19 принимает #114 как implementation delivery,
а #116 — как подготовку checklist и post-publication tooling. Production readiness
и публикация вынесены в отдельную release issue после ROADMAP DONE. Это не
разрешение на настройки, version bump, tags/releases, dispatch, публикацию
или main merge. Все условия publish остаются fail closed.

[Post-publication процедура](POST_PUBLICATION.md) получает независимый digest
из primary verify summary и устанавливает только совпавшие локальные артефакты.
Normal artifact CI проверяет tooling с явно локальным fixture на Linux/Windows;
`LOCAL_SIMULATION_PASS` не заменяет будущую проверку фактической версии на PyPI.
