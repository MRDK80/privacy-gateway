# Learning test progress

Progress is disabled by default. To locate parent boundaries in one admitted
learning measurement matrix case, use the canonical developer Python and:

```bash
PGW_TEST_LEARNING_PROGRESS=1 python -m pytest EXACT_MATRIX_NODE \
  --capture=tee-sys --durations=0 -vv --junitxml=FRESH_OUTPUT/result.xml
```

Replace the node/output placeholders before running. Admission and finite outer
supervision remain operator responsibilities; this command grants no authority.
Markers contain only fixed phase, start/end, numeric index/total and elapsed
seconds. They go to parent stderr; child stdout JSON and captured child stderr
remain unchanged. Each matrix case emits at most 68 lines (32 probes plus fixture
and active setup), each at most 128 bytes. Other opted-in tests add their own
fixture/active markers; this is not a suite-wide bound.

Elapsed phases overlap and are inclusive. No marker proves PASS or identifies
inside-child work; missing markers and silence mean unknown progress. Ordinary
emission errors are best-effort and may lose markers. Original operation errors
and cancellation remain failures; progress is not trusted workflow evidence.

Record an unchanged-source baseline separately using the same canonical Python,
`--durations=0 -vv` and JUnit. Compare only identical scenarios and disclose source,
environment, concurrency and instrumentation differences. A settings-removal
sample and a full matrix case cannot establish before/after speedup. No safe
speedup demonstrated is a valid result; do not cache proof or weaken assertions.

Reconcile expected fixed start/end counts before comparing phase timings. Missing,
unpaired or truncated markers and known clock/emission failures make phase
measurement incomplete; pytest/JUnit correctness remains separate. A clock failure
can remove both markers. End markers are not successful assertions.

## Representative measurements

On Linux with Python 3.12.3, one unchanged settings-removal failure-path baseline
passed in 83.06 s (pytest call: 77.62 s). The same scenario with progress passed in
83.12 s (call: 77.58 s); fixture was 1.651 s and inclusive active setup 64.467 s.
This single noisy pair establishes neither acceleration nor instrumentation
overhead. Measurements followed the terminal #375 gate, with no other local
heavy workload; hosted CI was separate. No paid model calls were used.

One executor-1 matrix case passed all 31 original fault probes in 790.55 s
(call: 785.09 s). Its 66 start/end markers paired correctly. Fixture was 1.656 s,
inclusive active setup 82.801 s, and probe totals summed to 691.174 s
(minimum 21.935 s, median 22.380 s, maximum 22.711 s). Fixture is nested inside
active setup; adding it again would double count. About 11.1 s of call time
remains outside active setup and probe totals. These parent observations do not
attribute inside-child Git or proof costs, cover all 157 probes, or provide an
uninstrumented matrix comparison.

Measured fixture materialization was small relative to the observed scenario.
No fixture-cache optimization is introduced: independent fault state and fresh
source-proof replays remain intact. No safe acceleration has been demonstrated.
