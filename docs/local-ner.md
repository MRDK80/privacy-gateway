# Local NER / Локальный NER

## English

ADR-473 selects the optional local RU/EN recognizer. It uses Gather INT8 and
two spaCy recheck models. To enable it, add the following section to the
existing entity configuration:

```yaml
local_ner:
  model_dir: ./models
```

The directory is resolved relative to the YAML file. Supply the exact
`Gather.onnx` and `tokenizer.json` from the approved evaluation bundle.
The operator installs ONNX Runtime 1.23.2, tokenizers 0.22.2, spaCy 3.8.18
and both `ru_core_news_sm` and `en_core_web_sm` 3.8.0 into the same
environment as Privacy Gateway. Use a separately prepared offline wheelhouse
and preserve the model license notices described in ADR-463 and the
installed spaCy model packages. Base installation has no NER extra.

The runtime verifies the artifacts when it opens the local session and
reuses that session in the process. Keep the provisioned model files fixed
while the process runs. Missing dependencies, digest mismatches at load,
and inference failures stop detection and preparation. Prepare also protects
selected Gather spans rejected by the recheck, including names with two
capitalized words and organizations with «…» quotes in predominantly Cyrillic input.
The independent validator still checks the resulting text. Review the protected text before
sending it onward. Evaluation includes both exact detection and the separate
measurement of exposed annotated values in successful preparations.

## Русский

ADR-473 утверждает опциональное локальное распознавание RU/EN. Связка
использует Gather INT8 и две модели перепроверки spaCy. Для явного
включения добавьте секцию из примера выше в существующий YAML сущностей.

Путь указывается относительно YAML. Поместите в него точные `Gather.onnx`
и `tokenizer.json` из комплекта оценки. В то же окружение, что и Privacy
Gateway, установите ONNX Runtime 1.23.2, tokenizers 0.22.2, spaCy 3.8.18 и
модели `ru_core_news_sm` и `en_core_web_sm` 3.8.0. Используйте заранее
подготовленный локальный набор wheel. Сохраните лицензионные уведомления
из ADR-463 и установленных пакетов моделей spaCy. Базовая установка
поставляется без extra для NER.

Хэши проверяются при открытии локальной сессии, которая переиспользуется
внутри процесса. Сохраняйте файлы комплекта неизменными во время работы.
При отсутствии зависимостей, несовпадении хэша при загрузке или сбое
инференса detect и prepare завершаются ошибкой. Prepare дополнительно
защищает часть отклонённых интервалов Gather, в том числе названия с двумя
заглавными словами и организации в кавычках «…» в преимущественно
кириллическом тексте. Независимый валидатор проверяет защищённый текст. Перед передачей проверьте его вручную.
Для оценки качества отдельно измеряются точные совпадения детектора и
открытые размеченные значения среди успешных подготовок.

[ADR-432](ADR-432-local-ner-integration.md) · [ADR-473](ADR-473-ner-org-selection.md)
