# 06_cli_round_trip — CLI end-to-end flow

[English](06_cli_round_trip.md) | [Русский](06_cli_round_trip.ru.md)

Prepare a request, simulate the external response locally, restore values and
clean artifacts. Only implemented commands/flags are used. This is not a script:
`pgw key create` changes the current account's system keyring. Automated tests in
`tests/test_examples_cli_round_trip.py` use an isolated keystore instead.

<!-- pgw-section: provider-boundary -->
## Safety requirements

No external provider is called. A deterministic local transformation receives
only protected `prompt.txt`. Routes/manifests stay local; key material is never
printed. Work files live in a dedicated directory removed during cleanup.

## Environment

```bash
pip install -e ".[dev]"
pgw key status
```

Missing-key status returns 3 and stderr `Key not found. Run 'pgw key create'.`.
CLI messages use the approved English-by-default contract (ADR-122).

```bash
pgw key create
```

Success returns 0 and stdout `Key created successfully.`. Repeated create without
`--force` returns 3 without silently overwriting the key.

## Step 1: synthetic request

Bash:

```bash
mkdir -p ./pgw_demo
cat > ./pgw_demo/request.txt <<'EOF'
Клиент user@example.com сообщил сбой на узле 192.0.2.10.
Контактный телефон +7 900 000-00-00.
EOF
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path .\pgw_demo | Out-Null
@'
Клиент user@example.com сообщил сбой на узле 192.0.2.10.
Контактный телефон +7 900 000-00-00.
'@ | Set-Content -Encoding utf8 .\pgw_demo\request.txt
```

The unchanged Russian fixture prose is intentional: `example.com`, documentation
address 192.0.2.10 and the test telephone value are synthetic repository fixtures,
not real input or a claim that arbitrary telephone numbers are reserved.

## Step 2: prepare

```bash
pgw prepare ./pgw_demo/request.txt --out ./pgw_demo/work
```

Exit 0, one stdout line:

```text
OK: <prompt.txt> / <route.json> / <manifest.json>
```

Three files appear directly in `./pgw_demo/work`, without subdirectories.
Prompt and route exclude original values; manifest values are encrypted.
Repeated prepare to the same directory without `--overwrite` returns 3 with
`BLOCKED:` in stderr, preserving existing artifacts.

## Step 3: simulate the response

Only prompt text crosses the provider boundary. Here a local status line is
appended without changing tokens. Bash:

```bash
cp ./pgw_demo/work/prompt.txt ./pgw_demo/reply.txt
printf '\nСтатус: заявка принята.\n' >> ./pgw_demo/reply.txt
```

PowerShell:

```powershell
Copy-Item .\pgw_demo\work\prompt.txt .\pgw_demo\reply.txt
Add-Content -Encoding utf8 .\pgw_demo\reply.txt "`nСтатус: заявка принята."
```

## Step 4: restore

```bash
pgw restore ./pgw_demo/reply.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/restored.txt
```

Exit 0; stdout is `OK: <path to restored.txt>`. Stderr reports token names/counts,
starting with `Restored: N/N tokens`, without source values. The output file
contains the original values and added status line, so it is sensitive. Without
`--out`, plaintext goes to stdout with no added newline. Relative manifest paths
resolve against the route directory; `--manifest` explicitly overrides the path.

<!-- pgw-section: strict-mode -->
## Step 5: fail closed

Strict mode is the default. A token absent from the manifest rejects the response:

```bash
cp ./pgw_demo/work/prompt.txt ./pgw_demo/bad_reply.txt
printf '\nСвяжитесь с [EMAIL_99].\n' >> ./pgw_demo/bad_reply.txt
pgw restore ./pgw_demo/bad_reply.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/bad_restored.txt
```

Exit 5; stderr begins `Строгий отказ по токенам:` without original values.
No bad-restored output file is created: no partial result is published.
`--lenient` instead warns and returns 0 for unknown/malformed tokens. Use it only
consciously for investigation; strict mode rejects instead of partially restoring
such a response.

<!-- pgw-section: cleanup -->
## Step 6: cleanup

Bash:

```bash
rm -rf ./pgw_demo
```

PowerShell:

```powershell
Remove-Item -Recurse -Force .\pgw_demo
```

The whole workspace, including manifest, is removed. The keyring key is unchanged;
there is no CLI key-deletion command. Key management is create/status/rotate.

## Exit/result contract

| Condition | Code | Observable result |
| --- | --- | --- |
| Existing key status |0 | Key-present stdout |
| Missing key status |3 | Missing-key stderr |
| Create key |0 | Created-key stdout |
| Repeated create |3 | Existing-key stderr |
| Prepare |0 | OK line, three artifacts |
| Prepare collision without overwrite |3 | BLOCKED stderr |
| Restore with output |0 | OK path stdout, report stderr |
| Strict unknown token |5 | Rejection stderr, no output file |
| Key unavailable |4 | Key-store stderr |

Argparse usage errors return 3, not 2:2 is reserved for `PENDING`.
Prompt/route and reports exclude original values; restored text exactly contains
them. Strict refusal creates no result. Cleanup removes the workspace. No network
is used. The subprocess tests run the same flow on Linux/Windows, substituting
keystore through isolated `PYTHONPATH` without real keyring or the user's home.

<!-- pgw-section: json-mode -->
## JSON automation

```bash
pgw --json prepare ./pgw_demo/request.txt --out ./pgw_demo/work
pgw --json key status
pgw --json restore ./pgw_demo/response.txt \
  --route ./pgw_demo/work/route.json \
  --out ./pgw_demo/restored.txt
```

This optional snippet uses `response.txt`; first save your intended protected-text
response there (the earlier flow used `reply.txt`). JSON restore requires `--out`:
plaintext is written only to that file, stdout is one operational JSON object.
Do not export route, manifest or restored output. See
[ADR-150](../docs/ADR-150-cli-json-contract.md).

<!-- pgw-section: catalog -->
## Machine-readable command catalog

Before any operational call, obtain the actual contract without help parsing:

```bash
pgw --describe
```

One JSON object describes commands, required parameters, machine types, choices,
formats, side effects, exit codes and machine codes. For example, select commands
supporting JSON before running them:

```bash
pgw --describe > ./pgw_demo/catalog.json
python - <<'PY'
import json

with open("./pgw_demo/catalog.json", encoding="utf-8") as handle:
    catalog = json.load(handle)

print(catalog["schema_version"], catalog["kind"])
for command in catalog["commands"]:
    if "json" in command["output_formats"]:
        print(command["id"], command["json_mode_requires"])
PY
```

Run the file-producing catalog example before Step 6 removes `pgw_demo` (or create
the directory again). The bare `pgw --describe` itself reads no request/route/
manifest, accesses no keyring and creates no files; shell redirection creates the
shown file. It may run before key creation. The catalog excludes key material,
source values, absolute local paths and environment values; the same code version
produces identical bytes. It reflects actual support: detect is human-readable
only, JSON restore needs an output path. See [ADR-151](../docs/ADR-151-cli-introspection.md).
