# ADR-256: финальный gate и завершение epic

Дата: 2026-09-28. Задача: #256. Epic: #248.

## Решение

Финал epic разделён на три доказательных этапа. `FINAL_GATE` закрепляет текущие
SHA `main` и roadmap, отсутствие открытых структурно связанных обязательных
tasks/follow-ups, полный local gate, review совокупного diff, evidence #259 и
подтверждённую владельцем реальную финальную consumer demo. Только после этого
возможен статус `ROADMAP READY FOR RELEASE`.

Roadmap PR допустим только из закреплённого roadmap SHA в `main`; перед merge
повторно требуются ровно пять exact checks `SUCCESS` этого head SHA. Изменение
`main`, roadmap SHA, base или появление нового blocking child возвращает процесс
в `FINAL_GATE`; конфликт не исправляется force push.

После merge отдельно закрепляются merge SHA и фактический новый SHA `main`.
`ROADMAP DONE`, закрытие epic и обновление его статуса допустимы только после
пяти `SUCCESS` checks post-merge CI нового `main`. PR CI не переносится на этот
этап. Сохранение защищённой roadmap-ветки явно отмечается, если её удаление не
разрешено.

## Fail-closed проверки

Неподтверждённая demo, открытый child, stale SHA, неполный local gate,
непроверенный cumulative diff, отсутствие verified lesson/negative prompt
tests, missing/pending/skipped/failed CI или неизвестный outcome внешнего write
блокируют следующий side effect. Повтор write разрешён только после read-only
reconciliation с доказанным `NOT_APPLIED`.

## Не входит

Production pilot и операторский E2E (#257), release/tag/PyPI, удаление
защищённой roadmap-ветки, изменение публичных Library/CLI контрактов и новые
architecture/security решения.
