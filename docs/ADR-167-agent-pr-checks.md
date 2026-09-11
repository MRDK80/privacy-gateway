# ADR-167. Проверка agent PR и GitHub checks (#167)

Дата: 2026-09-12. Epic: #162.

## Контекст

Локальный gate #163 не доказывает состояние GitHub Actions и не может
подтвердить, что checks относятся к неизменившемуся PR head. Текст PR,
комментарии, названия и вывод checks считаются недоверенными данными.

## Решение

`tools/agent_pr_checks.py` выполняет read-only проверку PR через `gh`:

- получает number, head/base refs, head SHA, URL, changed files и reviews;
- разрешает только task → `roadmap/*` либо `roadmap/*` → `main`;
- сверяет ожидаемые refs и SHA до и после чтения checks;
- ждёт terminal state всех applicable checks в ограниченном timeout;
- считает green только `SUCCESS`; pending продолжает ожидание, а skipped,
  neutral, cancelled, timed out и failure завершаются явным отказом;
- возвращает компактный JSON без stdout/stderr и без содержимого логов.

Failure evidence содержит только name, state и ссылку check/job. Это сохраняет
первичный источник, но не переносит потенциально чувствительные логи в agent
report. Полный набор checks не задаётся вторым статическим списком: applicable
checks берутся с точного PR head, а защита ветки остаётся источником required
checks. Пустой набор checks никогда не считается green.

Известные private artifact paths блокируются до оценки CI. Public agent
artifacts должны пройти существующие CI secret/privacy checks. Если diff
затрагивает instruction/configuration surface, green дополнительно требует
отдельный `APPROVED` GitHub review; controller verdict его не заменяет.
`.github/CODEOWNERS` назначает владельца для этой поверхности. Rulesets и
branch protection не ослабляются и не изменяются в рамках задачи.

Команда не создаёт PR, не комментирует, не push-ит и не merge-ит. Поэтому её
повторный запуск идемпотентен. Такие write-операции остаются отдельными явно
разрешаемыми действиями внешнего orchestrator. Auto-merge, merge без решения
человека, force и force-with-lease запрещены.

## Последствия и границы

Успешный результат доказывает состояние checks только для указанного PR и
точного SHA на момент финальной повторной сверки. Он не заменяет local gate,
GitHub Review или post-merge CI нового SHA. CODEOWNERS сам по себе не включает
enforcement на тарифе без поддержки; fail-closed проверка review остаётся в
инструменте.

**Статус:** действует.
