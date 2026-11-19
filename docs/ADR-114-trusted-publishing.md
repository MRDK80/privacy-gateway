# ADR-114. Проверяемая подготовка PyPI Trusted Publishing

Статус: trust-план утверждён владельцем 2026-11-19, реализация проверяется в #114.

## Решение

Один workflow publish-pypi.yml запускается только вручную через workflow_dispatch.
Режим по умолчанию dry-run. Source SHA — точный commit этого репозитория; tag для
publish обязан указывать на него и равняться v + canonical package version.
Publish допускается только из main workflow, source должен быть предком actual
main. Source и policy SHA разделены: trusted policy checkout использует точный
`github.workflow_sha`, а не moving main или source input.

Build job без OIDC собирает артефакты из source SHA и выполняет canonical build
smoke. Fresh verify job не исполняет код из source/дистрибутива, повторяет admission
и проверяет immutable artifact текущего run. Он использует trusted policy,
сверяет archive contents/license/version и байты модулей с выбранным source,
формирует evidence и загружает отдельный immutable verified artifact.

Publish job не делает checkout и не запускает package code. Он получает только
ID verified artifact текущего run и digest SHA256SUMS из outputs verify job,
проверяет digest и хеши точных файлов, затем вызывает SHA-pinned официальный
PyPA action. `id-token: write` существует только в publish job. Build/verify не
получают production credentials. Долгоживущие publishing tokens не вводятся.

## Environment admission

До допуска publish job primary GitHub API должен подтвердить public repository,
существующий environment pypi, required reviewer только MRDK80 (User id53867710),
`prevent_self_review: false` для единственного владельца и
`can_admins_bypass: false`. Deployment rule — ровно branch main, без tags и
других веток. Missing/403/unknown/private или неподтверждённое enforcement
блокируют publish; одно `environment: pypi` не считается доказательством защиты.
Environment автоматически не создаётся admission-кодом.

В environment остаётся отдельное явное approval каждого publish run. Это не
заменяет отдельного owner-разрешения фактических SHA/tag/release/publication.
PyPI identity: MRDK80/privacy-gateway, workflow publish-pypi.yml, environment pypi,
project privacy-gateway. Настройка identity и среды выполняется отдельно;
неизвестное владение проектом не интерпретируется как доступ к нему.

## Evidence и передача

Внутренний manifest содержит schema_version=1, repository, policy_sha,
source_sha, version, tag, mode, run_id, run_attempt, verify_job_id и список
{name,size,sha256} двух distributions. Canonical JSON сортирует ключи и заканчивается
newline. SHA256SUMS покрывает manifest и два точных distribution filenames;
его digest передаётся отдельным output trusted verify job. Нет самоссылки хеша. Wheel RECORD проверяется как строгий CSV:
ровно одна строка на каждый member, безопасные уникальные пути, sha256/size
соответствуют байтам; только собственная строка RECORD имеет пустые hash/size.
Перед каждым download проверяется один положительный числовой artifact ID:
пустой ID не может включить default download-all. Immutable artifact IDs и
run/attempt не берутся из manifest входного build.
Actual publish outcome фиксируется первичным job/action result; manifest до
публикации сам по себе не утверждает upload success.

Артефакты имеют ограниченный срок хранения, evidence нужно архивировать до expiry.
Partial upload не повторяется автоматически: сначала сравнить реальные PyPI
filenames/hashes, затем отдельное решение владельца. Не перемещать tag,
не перезаписывать version, не применять skip-existing как маскировку результата.

## Проверка и ограничения

PR проверяет workflow structure, guard failure paths и artifact validation с
синтетическими fixtures без публикации. До появления workflow в default branch
GitHub не обещает доступность его ручного dispatch: actual dry-run dispatch нельзя
выдавать за выполненный PR test. Настройки environment/PyPI, tags/releases,
main merge и публикация не входят в разрешение на реализацию #114.
Неизвестная защита среды остаётся явным readiness blocker.

Источники: [PyPI security model](https://docs.pypi.org/trusted-publishers/security-model/),
[PyPI publisher usage](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[GitHub environment API](https://docs.github.com/en/rest/deployments/environments),
[deployment review](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments).
