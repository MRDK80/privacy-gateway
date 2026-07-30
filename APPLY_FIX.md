# Как применить фикс кроссплатформенности

Архив содержит файлы с сохранением структуры репозитория `MRDK80/privacy-gateway`.

## Состав

| Файл | Действие | Что изменено |
|---|---|---|
| `src/privacy_gateway/detector.py` | Заменить | В `_RE_RESOURCE` добавлен POSIX-паттерн `/(?:[\w\-.]+/)+[\w\-.]+` |
| `tests/fixtures/synthetic/utf8_sample.txt` | Заменить | Добавлена строка с Linux-путём `/opt/synthetic-app/data/report.txt` |
| `tests/test_detector_crossplatform.py` | Добавить (новый) | 6 тестов: Linux-пути, Windows-пути, UNC, отсутствие ложного RESOURCE внутри URL |
| `.github/workflows/tests.yml` | Заменить | Матрица `ubuntu-latest` + `windows-latest`; detect-secrets отдельно для bash и pwsh |
| `docs/architecture.md` | Заменить | Убрано "Windows — основная целевая ОС"; добавлен раздел кроссплатформенных решений |
| `docs/detection.md` | Заменить | Добавлен раздел "Кроссплатформенность", обновлена таблица RESOURCE |
| `README.md` | Заменить | Требования, установка для Linux Mint (bash) и Windows (PowerShell) |

## Применение

### Linux / Linux Mint

```bash
cd /path/to/privacy-gateway
unzip -o ~/Downloads/privacy-gateway-crossplatform-fix.zip -d .
git status
git add -A
git commit -m "fix: cross-platform support for Linux Mint and Windows"
git push origin main
```

### Windows (PowerShell)

```powershell
cd C:\path\to\privacy-gateway
Expand-Archive -Path "$env:USERPROFILE\Downloads\privacy-gateway-crossplatform-fix.zip" -DestinationPath . -Force
git status
git add -A
git commit -m "fix: cross-platform support for Linux Mint and Windows"
git push origin main
```

## Проверка перед коммитом

```bash
pytest
ruff check .
mypy src
```

Ожидается: все тесты проходят, включая 6 новых в `tests/test_detector_crossplatform.py`.

## Замечания

- Файл `tests/test_detector.py` **не заменяется** — новые тесты вынесены в отдельный модуль, чтобы не конфликтовать с существующими.
- Если распаковка выполняется поверх рабочей копии, проверьте `git diff` перед коммитом.
- Архив не содержит `.env`, ключей, манифестов и реальных данных.
