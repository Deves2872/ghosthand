# Benchmark results — 20261124-034813

Model `claude-opus-5-5`, effort `medium`, timeout 300.0s per run, 5 runs per task from the same starting state. Outcomes checked on disk / in the apps, independently of the agent's message.

| task | level | runs | pass | timeout | said done but wasn't | focus moved | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|
| t1 Read a value (TextEdit) | easy | 4 | 4 | 0 | 0 | 0 | 9.3 | 12.2 |
| t2 Rename a file, contents intact (Finder) | easy | 5 | 5 | 0 | 0 | 0 | 16.3 | 16.6 |
| t3 Create and save a document (TextEdit) | easy | 5 | 5 | 0 | 0 | 0 | 28.3 | 29.3 |
| t4 Move flagged items in an 85-item list (Finder) | medium | 5 | 4 | 0 | 0 | 1 | 42.2 | 78.9 |
| t5 Edit one clause, save a copy, original intact (TextEdit) | medium | 5 | 5 | 0 | 0 | 0 | 27.4 | 31.3 |
| t6 Replace-file dialog interrupts save (TextEdit) | medium | 4 | 4 | 0 | 0 | 0 | 34.5 | 35.8 |
| t7 Finder list → TextEdit document, verified vs folder | hard | 5 | 5 | 0 | 0 | 0 | 38.3 | 43.9 |
| t8 Background edit+save while a person types in Stickies | hard | 5 | 5 | 0 | 0 | 0 | 11.7 | 12.3 |
| **all** | | 38 | 37 | 0 | 0 | 1 | 27.4 | 44.6 |

p50/p95 are nearest-rank over the runs in each row (n=38 overall). 2 run(s) never reached the agent (person app not frontable, or API billing error before the first call) and are excluded: t1#1, t6#2.
Agent reported 'failed' (honest failure): 1; no finish / harness error: 2; stale-state refusals by the server: 17.

## Failures

- **t4#3** agent said `failed` (37.66s, 10 calls): `{"moved_ok": false, "others_in_place": true, "diff": ["ledger-aug-067-hold.csv", "ledger-jul-006-hold.csv", "ledger-jul-018-hold.csv", "ledger-jul-054-hold.csv", "ledger-sep-008-hold.csv", "ledger-sep-080-hold.csv", "on-hold/ledger-aug-067-hold.csv", "on-hold/ledger-jul-006-hold.csv", "on-hold/ledger-jul-018-hold.csv", "on-hold/ledger-jul-054-hold.csv", "on-hold/ledger-sep-008-hold.csv", "on-hold/`

Tokens: 3075723 input (incl. cached) / 41550 output over 38 runs.
