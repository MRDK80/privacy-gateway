# ADR-251: task handover из ограниченного epic-мандата

Дата: 2026-09-27. Задача: #251. Epic: #248.

## Статус

Принято для fail-closed подготовки одной task-ветки и versioned handover.
GitHub delivery в эту задачу не входит.

## Решение

`tools/agent_epic_handover.py` принимает отдельно утверждённый локальный
epic-мандат версии `1.0` и однозначный task plan версии `1.0`. Канонический
SHA-256 digest мандата связывает repository, epic, единственную roadmap-ветку,
trusted policy SHA, operation allowlist и точный grant выбранной task. Текст
issue, task head и сам plan не могут изменить эти полномочия.

Task plan обязан быть подмножеством закреплённых `causal_scope` и
`allowed_paths`. Существующий файл допустим только по точному относительному
пути. Новый файл допустим только под уже существующим подтверждённым каталогом.
Абсолютный путь, `..`, symlink в любом компоненте, новый неподтверждённый
каталог и путь вне repository дают stop до branch write. Изменение policy или
архитектуры, включая `AGENTS.md`, `CONTRIBUTING.md`, security-документы и ADR,
требует отдельного решения владельца и не выводится из широкого path grant.

После локальных проверок инструмент вызывает существующий branch preflight из
ADR-235. Поэтому dirty tree, stale GitHub/local refs, неверный двусторонний
parent/sub-issue или конфликтующая ветка блокируют создание ref. Новый
executor не вводится: результат является handover `1.0` для существующего
workflow ADR-236 с deny-by-default permissions и обязательным полным gate.
Дополнительный `mandate_provenance` закрепляет schema version, digest и policy
SHA и входит в plan digest handover.

## Последствия

- Любое изменение mandate или task plan меняет соответствующий digest и
  требует нового approval.
- Успешный результат создаёт только локальный task ref без checkout/upstream и
  возвращает JSON handover; commit, push, PR, comment, merge и issue write не
  выполняются.
- Инструмент внутренний и не меняет публичные Library/`pgw` API, существующие
  публичные JSON contracts, версию пакета или release process.

## Не входит

Хранение/выдача/отзыв мандата, checkout task-ветки, запуск production Codex,
delivery driver, GitHub write, consumer demo, runtime epic loop, version bump,
release и публикация.
