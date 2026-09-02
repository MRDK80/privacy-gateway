# ADR-86: SPDX license metadata и нижняя граница setuptools

- Статус: Accepted
- Дата: 2026-09-02
- Issue: [#86](https://github.com/MRDK80/privacy-gateway/issues/86)
- Связанные решения: [ADR-81](ADR-81-single-source-package-version.md), [#85](https://github.com/MRDK80/privacy-gateway/issues/85)

## Контекст

`pyproject.toml` использует устаревающую TOML-таблицу лицензии и license classifier:

```toml
license = {text = "MIT"}
classifiers = [
  "License :: OSI Approved :: MIT License",
]
```

Актуальный setuptools предупреждает, что TOML-таблица `project.license` и license classifiers устарели. PEP 639 задаёт SPDX license expression и отдельное объявление license files. Для setuptools целевая форма требует backend семейства 77 или новее, тогда как решение #85 фиксировало нижнюю границу `setuptools>=70.1` и boundary-пин `70.1.0`.

Это build-time решение. Оно не меняет поддерживаемые runtime-версии Python, production-код или механизм единственного источника версии из ADR-81.

## Решение

Миграция выполняется сейчас. Нижняя граница build backend повышается до точной минимальной поддерживаемой версии `setuptools>=77.0.3`:

```toml
[build-system]
requires = ["setuptools>=77.0.3", "wheel"]

[project]
license = "MIT"
license-files = ["LICENSE"]
```

Classifier `License :: OSI Approved :: MIT License` удаляется. Лицензия проекта и содержимое файла `LICENSE` остаются MIT без изменений.

Boundary cell из #85 сохраняется на Ubuntu / Python 3.11, но точный пин меняется с `70.1.0` на `77.0.3`. Основная CI-матрица Ubuntu/Windows × Python 3.11/3.12 и отдельный pre-commit job не меняются.

## Почему 77.0.3

Официальная поддержка SPDX license expressions и `project.license-files` появилась в семействе 77. Версия `77.0.0` отсутствует в использованном package index; первая доступная версия — `77.0.1`.

Disposable boundary build доказал, что `77.0.1` выполняет текущий happy-path проекта. Тем не менее поддерживаемой нижней границей выбрана `77.0.3`: она также полностью проверена исполнением и является стабилизационным patch-релизом новой license-metadata реализации. Повышение на две patch-версии уменьшает риск зависеть от первой доступной реализации и не влияет на runtime Python matrix.

## Исполненное доказательство

Замеры выполнены из disposable copy commit `fd33989c77ad2b989db2371c600045cb803b0b43` с frontend `build==1.6.0`. После прогонов tracked checkout остался чистым.

### До исправления

- Isolated build с backend `setuptools 84.0.0` воспроизводит предупреждения про TOML-table `project.license` и license classifiers.
- Boundary builds текущей конфигурации на `77.0.1` и `77.0.3` также воспроизводят оба предупреждения.
- Текущая конфигурация создаёт устаревшее поле `License: MIT`, а не `License-Expression`.
- На `70.1.0` LICENSE размещается в `.dist-info/LICENSE`, а в семействе 77 и актуальном isolated build — в `.dist-info/licenses/LICENSE`.

### Целевая конфигурация

PEP 639-вариант успешно собран в isolated-режиме с backend `84.0.0` и в boundary-режиме с `77.0.1` и `77.0.3`. Для `77.0.3` подтверждено:

- sdist и wheel, собранный из sdist;
- `WHEEL Generator: setuptools (77.0.3)`;
- `METADATA Version: 0.5.0`;
- `License-Expression: MIT`;
- `License-File: LICENSE`;
- отсутствие устаревшего поля `License`;
- отсутствие license classifiers;
- LICENSE в корне sdist;
- LICENSE в `privacy_gateway-0.5.0.dist-info/licenses/LICENSE` внутри wheel;
- отсутствие обоих целевых `SetuptoolsDeprecationWarning`.

### Негативные проверки

- `setuptools 76.1.0` отвергает строковый `project.license` при `get_requires_for_build_sdist`; sdist и wheel не создаются.
- `setuptools 70.1.0` отвергает ту же конфигурацию аналогично.
- SPDX-конфигурация с сохранённым MIT license classifier на `77.0.3` падает с `InvalidConfigError`; удаление classifier обязательно.
- Временное нарушение ожидаемых SPDX/license-file инвариантов должно делать packaging test или build verifier красным.

## Отклонённые варианты

### Сохранить setuptools>=70.1

Отклонено. Версии `70.1.0` и `76.1.0` не принимают целевую строковую SPDX-конфигурацию. Отсрочка сохраняет предупреждения и не обеспечивает требуемый artifact contract PEP 639.

### Использовать setuptools>=77.0.1

Отклонено как поддерживаемый floor, хотя happy-path проекта на этой версии исполненно зелёный. Версия `77.0.3` даёт проверенную стабилизированную границу ценой только двух patch-релизов и без изменения runtime-совместимости.

### Промежуточный механизм

Отклонено. Два режима license metadata усложнили бы конфигурацию и доказательство совместимости без продуктовой пользы. Старые backend не могут обработать целевую форму, поэтому прозрачного совместимого промежуточного состояния нет.

## Контракт артефактов

Build gate обязан проверять не только успешность сборки, но и итоговые артефакты:

1. Собираются ровно один sdist и один wheel из sdist.
2. Wheel metadata содержит `License-Expression: MIT`.
3. Wheel metadata содержит ровно ожидаемый `License-File: LICENSE`.
4. Устаревшее поле `License` и license classifiers отсутствуют.
5. LICENSE присутствует в корне sdist.
6. LICENSE присутствует в wheel по пути `*.dist-info/licenses/LICENSE`.
7. В build output отсутствуют предупреждения про TOML-table `project.license` и license classifiers.
8. `importlib.metadata.version("privacy-gateway")` и `privacy_gateway.__version__` равны `0.5.0`.
9. Сборка не изменяет tracked checkout.
10. Boundary-сборка использует фактический backend `77.0.3`, подтверждённый полем Generator.

Инварианты формы `pyproject.toml` закрепляются packaging-тестом. Проверки итоговых metadata и расположения LICENSE выполняются `tools/verify_package_build.py`. Негативный тест обязан доказать, что нарушение SPDX или license-file контракта обнаруживается.

## Последствия

Положительные последствия:

- metadata соответствует PEP 639;
- исчезают целевые deprecation warnings;
- sdist и wheel явно несут MIT license file;
- нижняя граница backend проверяется отдельной CI boundary cell;
- runtime API, CLI и версия пакета не меняются.

Ограничения:

- сборочные окружения с setuptools 70.1–77.0.2 больше не поддерживаются;
- изменение требует синхронного обновления `pyproject.toml`, CI boundary cell, build verifier, packaging-тестов и документации;
- обычный isolated build не доказывает нижнюю границу, поэтому точный boundary-пин остаётся обязательным.

## Scope реализации

В scope входят:

- `pyproject.toml`;
- `.github/workflows/tests.yml`;
- `tools/verify_package_build.py`;
- packaging-тесты;
- `CONTRIBUTING.md`;
- `CHANGELOG.md`;
- индекс решений, если он перечисляет ADR.

Вне scope остаются:

- production-код в `src/`;
- версия `0.5.0`;
- tags, GitHub Release и PyPI publishing;
- содержимое MIT-лицензии;
- механизм ADR-81;
- unrelated pre-commit hooks, EOL-политика и `.gitattributes`.

## Проверка решения

До готовности к review выполняются обязательные локальные проверки репозитория и normal/boundary build gate. GitHub Actions должен подтвердить PR HEAD по полной матрице Ubuntu/Windows × Python 3.11/3.12 и отдельному pre-commit job. Локальные проверки не заменяют GitHub Actions.
