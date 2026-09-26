# ADR-196: trusted local snapshot проверяемого pilot diff

**Статус:** действует.

**Контекст.** Production pilot #182 показал, что при незакоммиченном результате
executor значения `base_sha` и `head_sha` совпадают: `_head_sha()` возвращает
`git rev-parse HEAD`, то есть идентичность коммита, а не состояние изменённого
рабочего дерева. `_changed_files()` видит untracked-файлы, но идентичности им не
даёт. Deterministic gate получает имена веток, а `_diff()` читает дерево
отдельным проходом, поэтому gate и controller нельзя связать с одним
проверенным набором файлов. После удаления disposable worktree доказательство
исчезает.

**Решение.** Оркестратор создаёт trusted local snapshot после успешной
post-execution проверки scope и до deterministic gate:

```text
executor
  -> post-execution scope validation
  -> trusted local snapshot
  -> deterministic gate
  -> verify tree unchanged
  -> controller
```

Механизм: disposable bare clone во временном каталоге вне репозитория,
отдельный индекс через `GIT_INDEX_FILE`, `read-tree <base_sha>`, добавление
разрешённого состояния вместе с untracked-файлами, `write-tree` для
`tree_hash`, `commit-tree` для `snapshot_commit` без перемещения refs и
`diff --binary` для `diff_sha256`. Push отсутствует, временный каталог
удаляется.

**Идентичность коммиттера.** `commit-tree` берёт автора и коммиттера из
переменных окружения, а при их отсутствии — из конфигурации Git. На CI-раннерах
глобальная идентичность не настроена, поэтому snapshot задаёт
`GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_AUTHOR_DATE`, `GIT_COMMITTER_NAME`,
`GIT_COMMITTER_EMAIL` и `GIT_COMMITTER_DATE` явными синтетическими значениями с
фиксированными датами.

**Воспроизводимость.** `tree_hash` детерминирован для одинакового содержимого
индекса. `snapshot_commit` детерминирован благодаря фиксированным датам и
идентичности. `diff_sha256` считается по `git diff --binary` в bytes-режиме;
кросс-платформенное равенство не гарантируется, пока не зафиксирована политика
перевода строк: на Windows `core.autocrlf` обычно включён, а `.gitattributes` в
репозитории отсутствует.

**Контракт evidence.** `SnapshotEvidence` содержит `base_sha`,
`snapshot_method`, `snapshot_commit`, `tree_hash`, `diff_sha256`,
`provenance_complete` и нормализованный `allowed_paths`. Канонические схемы
`executor-report` и `controller-verdict` не изменяются: они закрыты
`additionalProperties: false`, а оркестратор дополнительно требует точного
равенства множеств ключей. Публичный stdout `RunResult` и exit codes 0 и 20
также не изменяются. Размещение provenance в `gate_evidence` и полный
versioned контракт — задача #197.

**Machine codes.** `SNAPSHOT_FAILED` — недоступность Git, ненулевой код или
ошибка подпроцесса. `SNAPSHOT_SCOPE_VIOLATION` — protected, policy или
out-of-scope путь, а также пустой allowlist. `TREE_MUTATED_AFTER_SNAPSHOT` —
расхождение дерева со snapshot при повторной проверке. Существующие коды и
поведение не переопределяются.

**Границы.** Executor и controller не получают `commit`, `push`, `create_pr`,
`comment` и `merge`. Refs локального и удалённого репозитория не перемещаются.
Snapshot не является OS-level writable confinement — это #185. Evidence не
содержит путей рабочего каталога, приватного хранилища, логов, prompts,
credentials и PII.

**Альтернативы.** Linked worktree отклонён: он использует общие Git metadata и
object store. Обычный локальный commit в disposable clone допустим, но двигает
локальный ref. Документирование совпадения `base_sha` и `head_sha` отклонено:
это отсутствие provenance.

**Последствия.** Каждый snapshot создаёт временный bare clone, поэтому растёт
стоимость run на больших репозиториях. Требуется отдельный bytes-runner, так
как существующий `_run()` текстовый и обрезает вывод. Поведение на Windows
подтверждается тестами CI-матрицы. Остаточный риск: временные Git-объекты живут
до удаления каталога.
