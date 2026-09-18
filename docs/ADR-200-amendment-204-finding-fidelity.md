# ADR-200 amendment — задача #204: точность редакции findings и история итераций

Статус: accepted (решение владельца). Ревизия 2, приведена к фактическому коду.
Область: приватная retrospective запись agent orchestrator.
Родительский ADR: `docs/ADR-200-persist-gate-evidence.md`.
Связанные issue: #204 (bug), parent epic #179.

Amendment уточняет форму хранения blocking findings и историю repair-цикла.
Граница слоёв ADR-200 не пересматривается: вердикт и findings остаются только
в приватной retrospective, публичная запись прогона содержит исключительно
детерминированные измерения.

## Контекст

Живой прогон `b4c8a28ea62a48898b0feb4502167548` (exit 20, `FAIL_ESCALATE`,
`findings: 7`, `verdicts: ["FAIL_RETRY", "FAIL_RETRY", "FAIL_ESCALATE"]`,
`escalation_reason: budget_exhausted`) выявил два дефекта персистентности.

Причины установлены чтением кода, а не гипотезами:

- `FINDING_SEVERITIES` в `tools/agent_orchestrate.py` содержал
  `blocking|major|minor|info` и не пересекался с каноническим enum
  `critical|high|medium|low`, поэтому `severity` любой валидной находки
  становился `"unknown"`;
- `SUMMARY_KEYS` содержал `summary|message|description|title|detail` и не
  содержал канонических `requirement`, `evidence`, `required_fix`, поэтому
  текст не доходил до фильтра символов и `summary_dropped` был истинным
  всегда;
- локация искалась только по `PATH_KEYS` в корне finding, где каноническая
  схема запрещает дополнительные свойства, а ключ `location` не читался
  вовсе, поэтому `location.path` был `null` при allowlist из единственного
  `README.md`;
- `_record_verdict` присваивал `state["verdict"]`, а не накапливал историю,
  поэтому сохранялась только последняя итерация.

## D1. Раздельные redacted-поля

`requirement`, `evidence` и `required_fix` сохраняются отдельными
redacted-полями. Каждое поле редактируется независимо: отказ одного не удаляет
остальные.

Legacy-поле `summary` **сохранено**, а не удалено. Оно наполняется прежними
`SUMMARY_KEYS` и не является конкатенацией новых полей. Причина: от формы
находки зависят одиннадцать тестовых модулей, а аддитивное изменение оставляет
и старые записи, и существующие тесты валидными.

Правила:

- `severity` сохраняется из канонического enum `critical|high|medium|low`;
  унаследованный набор `blocking|major|minor|info` продолжает приниматься;
  `"unknown"` остаётся только когда поле отсутствует или лежит вне обоих
  наборов;
- `location.path` восстанавливается из `location.file`, а `line_start` — из
  `location.line`; абсолютные и платформенные пути по-прежнему отклоняются;
- `finding_fingerprint` сохраняется независимо от результата редакции;
- к `evidence` применяется вдвое более жёсткий лимит длины
  (`EVIDENCE_TEXT_MAX_CHARS = SUMMARY_MAX_CHARS // 2`, то есть 100 против
  200): это поле ближе всего к цитате кода и вывода инструментов;
- `redaction.version` повышен с `"1"` до `"2"`; форма `"1"` остаётся валидной
  при чтении;
- `redaction.summary_dropped` сохранён и означает «`summary` отсутствует и все
  три текстовых поля удалены»;
- `redaction.dropped_fields` — отсортированный список имён удалённых полей;
- `redaction.drop_reasons` использует закрытый перечень из пяти значений:
  `missing`, `disallowed_chars`, `path_like`, `unparseable` и отдельная
  причина для срабатывания детектора секретоподобных значений. Точные
  идентификаторы заданы константой `DROP_REASONS` в
  `tools/agent_orchestrate.py`; расширять перечень в записи запрещено.
  Свободный текст в причинах запрещён;
- `FINDING_REDACTION_FAILED` продолжает применяться к неразбираемым объектам;
  такая находка получает все три поля `null`, причину `unparseable` и
  `summary_dropped: true`.

Форма находки в приватной записи:

```json
{
  "severity": "high",
  "category": "unclassified",
  "check_id": null,
  "finding_fingerprint": "sha256:...",
  "location": {"path": "README.md", "line_start": 12, "line_end": null},
  "summary": null,
  "requirement": "<redacted text or null>",
  "evidence": null,
  "required_fix": "<redacted text or null>",
  "redaction": {
    "applied": true,
    "version": "2",
    "summary_dropped": false,
    "dropped_fields": ["evidence"],
    "drop_reasons": {"evidence": "disallowed_chars"}
  }
}
```

### Достижимость причин отказа

`SUMMARY_ALLOWED_CHARS` содержит только буквы, цифры, пробел и `_=,.:+-`.
Отсюда два наблюдения, зафиксированных тестами:

- проверки `startswith(("/", "~", "\\"))` и `"\\" in filtered`
  недостижимы: все три символа отсутствуют в алфавите и вырезаются до
  проверки. Текст `/etc/hosts must be reverted` сохраняется как
  `etchosts must be reverted`, то есть структура пути исчезает, а поле
  остаётся;
- единственная достижимая причина `path_like` — последовательность `..`,
  поскольку точка в алфавит входит;
- усечение по длине не приводит к отказу, поэтому причины `length_exceeded`
  не существует.

## D2. schema_version приватной записи: 1.2

История итераций — аддитивное структурное изменение формата, поэтому версия
повышена с `1.1` до `1.2`.

Правила:

- writer создаёт только `1.2`;
- reader принимает `1.0`, `1.1` и `1.2`;
- версия разбирается по числовым компонентам функцией `_parse_version`, а не
  строковым сравнением: `"1.10"` даёт `(1, 10)`;
- чужой major отклоняется fail-closed;
- неизвестный minor при известном major **также отклоняется fail-closed**.
  Это отклонение от первоначального решения: все валидаторы приватной записи
  сравнивают наборы ключей строгим равенством, поэтому чтение с игнорированием
  незнакомых полей архитектурно невозможно без более широкой переделки;
- `PUBLIC_RECORD_SCHEMA_VERSION` остаётся `1.0`;
- `PROMOTION_SCHEMA_VERSION` остаётся `1.0`, порог `evidence_count >= 2`
  не меняется.

### Размещение истории

История хранится как `iteration_history` **внутри** verdict evidence, а не
рядом с ним. Следствия:

- `EVIDENCE_KEYS` остаётся четырёхключевым (`gate`, `verdict`, `snapshot`,
  `durations`);
- `private_evidence` и `public_evidence` не изменены;
- публичная проекция прогона не расширена, правило Р2 соблюдено;
- накопитель `verdict_history` живёт в служебном состоянии прогона и в запись
  не попадает.

```json
{
  "schema_version": "1.2",
  "evidence": {
    "verdict": {
      "verdict": "FAIL_ESCALATE",
      "review_basis": {},
      "escalation_reason": "budget_exhausted",
      "blocking_findings": [],
      "iteration_history": [
        {"iteration": 0, "verdict": "FAIL_RETRY", "blocking_findings": []},
        {"iteration": 1, "verdict": "FAIL_RETRY", "blocking_findings": []},
        {"iteration": 2, "verdict": "FAIL_ESCALATE", "blocking_findings": []}
      ]
    }
  }
}
```

Инварианты:

- номер итерации берётся из канонического `repair_iteration` вердикта, с
  откатом на позицию в истории;
- номера неотрицательны и не убывают;
- вердикт каждой итерации принадлежит `ALLOWED_VERDICTS`;
- каждая находка истории проходит полную валидацию finding;
- `blocking_findings` верхнего уровня остаётся проекцией последней итерации;
- сумма находок по итерациям равна счётчику `findings` прогона;
- одинаковые `finding_fingerprint` в разных итерациях не дедуплицируются:
  именно повтор отличает нерешённое замечание от нового.

## D3. Диагностический живой прогон

Прогон с `--max-repairs 0` до исправления не выполнялся. Редакция применяется
до записи, поэтому такой прогон воспроизвёл бы уже задокументированный
симптом, но не показал бы утраченный текст. Причины установлены чтением
реализации и зафиксированы синтетическими regression-тестами.

Живая проверка выполняется после merge и требует минимум одной
repair-итерации: `--max-repairs 0` структурно не проверяет сохранение истории.

## D4. Pilot-worktree

Незакоммиченная правка `README.md` из pilot-worktree прогона `b4c8a28e` не
коммитилась: она трижды не принята контроллером и не проходила review. Diff
сохранён как диагностический артефакт вне репозитория, worktree удалён
штатно через `git worktree remove --force` и `git worktree prune`.

## Проверяемость

Реализовано тремя коммитами: `00fbcf5` (severity и location), `384eaad`
(раздельные поля), `6e1dfae` (история итераций и версия 1.2).

Покрытие тестами, `tests/test_agent_finding_fidelity_204.py`,
`tests/test_agent_finding_fields_204.py`,
`tests/test_agent_iteration_history_204.py`:

- сохранение всех четырёх канонических `severity` и откат на `"unknown"`
  вне обоих наборов;
- восстановление `location.path` и `line_start`, отклонение абсолютного пути;
- независимость редакции: секретоподобный `evidence` удаляется, а
  `requirement` и `required_fix` сохраняются;
- причины отказа `missing`, `path_like` и срабатывание детектора
  секретоподобных значений;
- более жёсткий лимит `evidence` относительно `requirement`;
- совпадение формы находки с `FINDING_KEYS_V2` и `REDACTION_KEYS_V2`;
- fail-closed для неразбираемой находки;
- полнота истории из трёх итераций и равенство суммы находок счётчику;
- отсутствие дедупликации повторных отпечатков;
- числовой разбор версии и отклонение неразбираемых значений.

Локальный quality gate на содержимом `6e1dfae`, `/home/soltator/privacy-gateway`,
Python 3.12, `.venv`:

| Команда | Результат | Exit code |
|---|---|---|
| `ruff check .` | All checks passed | 0 |
| `pytest -q` | 831 passed, 7 skipped, failed 0 | 0 |
| `mypy .` | no issues found in 99 source files | 0 |
| `pre-commit run --all-files` | пять хуков Passed | 0 |

Приватность сохранена: raw model output, prompts, chain-of-thought,
credentials, PII, `log_directory` и абсолютные пути не сохраняются; публичные
fixtures синтетические.
