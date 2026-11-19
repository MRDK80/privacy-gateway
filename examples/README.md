# Privacy Gateway examples

[English](README.md) | [Русский](README.ru.md)

Reproducible public-library examples run locally on synthetic data without
network calls. `05_key_rotation.md` and `06_cli_round_trip.md` are command guides,
not executable scripts; key management/rotation affects the current system keyring.

<!-- pgw-section: provider-boundary -->
## Safety requirements

- Use only public `privacy_gateway` APIs, not internal modules.
- Use only synthetic values: `user@example.com`, `192.0.2.10`, `2001:db8::1`,
  `+7 900 000-00-00`, `https://example.com/status` and dictionary fixtures in
  `config.example/entities.yaml` (`Синтетический Иванов`, `ООО Северный Маяк`,
  `Проект-Орион`, `Технический директор`, `staging-test`). Fixture strings are
  intentionally unchanged by translation.
- Only protected `prepared.text` crosses the provider boundary. Never print or
  export context, manifests, routes or key material. Restored text is sensitive.
- Examples invoke `discard()` in `finally`; this is an observed tested path,
  not a promise that cleanup can never fail.

## Environment

Run from a separate repository checkout; wheels/pipx do not install these examples.
See the [installation guide](../docs/INSTALLATION.md) for packaged CLI/library
choices. For the documented developer checkout:

```bash
pip install -e ".[dev]"
pgw key create
```

An active system key is required for manual runs; absence produces a key-store
failure (fail closed). Automated tests substitute an in-memory key and disable
network/real keyring access; they do not establish real-vault readiness.

<!-- pgw-section: quickstart -->
## 01_quickstart.py

Minimal `prepare` → local deterministic response → `restore` → `discard`.

```bash
python examples/01_quickstart.py
```

Expected six `key=value` lines:

```text
tokens_prepared=<N>
tokens_restored=<N>
tokens_missing=0
protected_leak_free=True
roundtrip_exact=True
workspace_clean=True
```

Exit 0, matching prepared/restored counts, zero missing tokens, no supported source
values in protected text, exact restored response and empty workspace are checked.
Output contains no values, artifact paths or key material. `<N>` depends on
`config.example/entities.yaml`; relationships, not a fixed count, are the contract.
`tests/test_examples_quickstart.py` runs the actual file without network, real
keyring or the user's home and checks cleanup on injected failures.

## 02_full_round_trip.py

Detection/policy → prepare → local provider → validation/restore → discard on a
synthetic letter. Dictionary types: `PERSON`, `ROLE`, `ORG`, `PROJECT`,
`ENVIRONMENT`; regex types: `EMAIL`, `PHONE`, `HOST`, `ENDPOINT`, `DATE`, `AMOUNT`.

```bash
python examples/02_full_round_trip.py
```

It displays the synthetic original, protected provider input, provider response,
restored response and public statistics:

```text
tokens_prepared=<N>
tokens_restored=<N>
tokens_missing=0
protected_leak_free=True
unsupported_document_visible=True
roundtrip_exact=True
workspace_clean=True
```

Exit 0 requires a pure deterministic single-argument provider receiving only
protected text, no supported values leaking in that text, matching counts, exact
restoration and cleanup. The `DOCUMENT` contract number intentionally stays visible:
this unsupported class is a detector limitation, not protected real document data.
No manifest/route/workspace paths or key material are printed.

There is no public `validate()` method. `prepare` performs detection/blocking and
raises `DetectionError` without a protected payload on failure. `restore` validates
strictly: unknown/malformed tokens raise `StrictTokenError`; bad artifact integrity
raises `IntegrityError`. See [tokens](../docs/token-format.md) and
[library API](../docs/LIBRARY_API.md). `tests/test_examples_full_round_trip.py`
checks provider boundaries and cleanup when provider/restore fails.

## 03_blocked_secret.py

Negative fail-closed example: ordinary pseudonymizable entities plus an explicitly
synthetic keyword-pattern secret (`пароль: <значение>`). Secret priority blocks the
whole prepare operation: `DetectionError`/`BLOCKED`, no protected payload, no spy
provider calls, empty workspace.

```bash
python examples/03_blocked_secret.py
```

Expected seven lines:

```text
secret_blocked=True
failure_class=DetectionError
failure_status=BLOCKED
provider_calls=0
protected_payload_created=False
pseudonymizable_entity_present=True
workspace_clean=True
```

Exit 0 means expected blocking/no payload/no provider call; exit 1 means an invariant
failed and is reported as statistics without traceback; exit 2 means wrong cwd.
The ordinary entity remains present but the secret finding determines rejection.
Output excludes secret/input/exception text, artifacts and paths. The key is read
before detection; a missing key gives separate `KeyStoreError`, not this blocking
scenario. `tests/test_examples_blocked_secret.py` proves the spy would fail if
called, the same input without the secret succeeds, and output is deterministic.

## 04_untrusted_response.py

One synthetic request, five response variants (retained, removed, duplicated,
malformed and invented token), each in strict and lenient mode. Classification
and rejection happen inside `restore` before substituting source values.

```bash
python examples/04_untrusted_response.py
```

Ten variant rows and four summary rows:

```text
variant=<имя> mode=<strict|lenient> outcome=<restored|StrictTokenError> ...
provider_calls=10
protected_leak_free=True
workspace_clean=True
all_variants_expected=True
```

`<имя>` is the original placeholder for a variant name, not translated output.
Let `<T>` be the prepared token count:

| Variant | Strict | Lenient |
| --- | --- | --- |
| Retained | restored, `<T>` restored /0 missing | Same |
| Removed | restored, `<T-1>` restored /1 missing | Same |
| Duplicated | restored, `<T>` restored /0 missing | Same |
| Malformed | StrictTokenError, no plaintext | restored, `<T-1>` restored /1 missing |
| Invented | StrictTokenError, no plaintext | restored, `<T>` restored /0 missing |

Exit 0 means all variants match expectations, provider receives only protected text
and every workspace is cleaned; exit 1 reports an invariant failure without
traceback; exit 2 means wrong cwd. Removed tokens differ from unknown tokens:
missing counts do not themselves cause rejection. A known duplicate is restored
at every occurrence; restored count is unchanged, and the example detects this
anomaly itself. Invented values are never restored; lenient mode leaves the
invented token literal. `discard()` is called in `finally` for every variant.
No source values, token names, context, artifact names or paths are printed.

`RestoredPayload` exposes only `text`, `correlation_id`, `tokens_restored` and
`tokens_missing`, not candidate lists, duplicate counters or warnings. Unbracketed
`PROJECT_1` is not a candidate: it is invisible to restore and counts as missing,
not malformed. Lenient mode leaves unknown/malformed candidates and continues;
it is for human investigation, not a safe automatic pipeline.
`tests/test_examples_untrusted_response.py` checks all ten cases, cleanup,
determinism and absence of source values in output.

<!-- pgw-section: rotation -->
## 05_key_rotation.md

See the [rotation guide](05_key_rotation.md) for restoring a request from the
previous key. Rotation irreversibly changes the current account's keyring and
must not be run against a working keyring without a deliberate decision.

```bash
pip install -e ".[dev]"
pgw key status
```

Commands in the scenario:

```bash
pgw prepare request.txt --out ./work
pgw key rotate
pgw restore reply.txt --route ./work/route.json
pgw key rotate
```

Exit 0: key create/status/rotate success. Exit 3: status without a key, repeated
create without `--force`, or restore beyond retention. Exit 4: key-store failure,
rotate without an active key, carry/active-write/verification/prune failure.
K0 remains readable after one rotation, not two. The API window is immediately
`[active, retired]`; physical retention changes only through explicit operations.
`--force` is reset, not rotation: it removes retired history. Rotation is not a
keyring transaction; verification/prune failure is not rollback. ADR-23/ADR-46
and the [security guide](../docs/SECURITY.md) define this boundary.
`tests/test_documentation_contracts.py` verifies real subcommands, the generation
table and bounded retention references; key tests use a fake backend.

<!-- pgw-section: cli-flow -->
## 06_cli_round_trip.md

See the [CLI guide](06_cli_round_trip.md) for request preparation, a local simulated
response, restoration and cleanup, with Bash/PowerShell differences.

```bash
pip install -e ".[dev]"
pgw key status
```

Commands in the scenario:

```bash
pgw prepare ./pgw_demo/request.txt --out ./pgw_demo/work
pgw restore ./pgw_demo/reply.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/restored.txt
rm -rf ./pgw_demo
```

Exit 0: prepared three artifacts or restored output. Exit 3: collision without
`--overwrite`, invalid input/configuration or argparse usage. Exit 4: unavailable
key or disallowed backend. Exit 5: strict rejection of unknown/malformed tokens.
Prompt/route and token reports exclude source values, restored text contains them,
strict failure publishes no output, and cleanup removes the working directory.
The scenario makes no network calls. Isolated tests also avoid the user's home
and real system keyring: `tests/test_examples_cli_round_trip.py` runs subprocesses
on Linux/Windows with a test-only keystore through isolated `PYTHONPATH`.

## Further examples

[Epic #96](https://github.com/MRDK80/privacy-gateway/issues/96) tracks examples.
No currently planned scenario is represented as delivered without its file.
