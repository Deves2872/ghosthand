# Benchmark results — 20261124-041242

Model `claude-opus-5-5`, effort `medium`, timeout 300.0s per run, 5 runs per task from the same starting state. Outcomes checked on disk / in the apps, independently of the agent's message.

| task | level | runs | pass | timeout | said done but wasn't | focus moved | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|
| t1 Read a value (TextEdit) | easy | 5 | 5 | 0 | 0 | 0 | 10.1 | 12.2 |
| t2 Rename a file, contents intact (Finder) | easy | 5 | 5 | 0 | 0 | 0 | 14.3 | 15.4 |
| t3 Create and save a document (TextEdit) | easy | 5 | 5 | 0 | 0 | 0 | 29.9 | 33.3 |
| t4 Move flagged items in an 85-item list (Finder) | medium | 5 | 5 | 0 | 0 | 0 | 29.6 | 31.1 |
| t5 Edit one clause, save a copy, original intact (TextEdit) | medium | 5 | 5 | 0 | 0 | 0 | 42.3 | 44.7 |
| t6 Replace-file dialog interrupts save (TextEdit) | medium | 5 | 5 | 0 | 0 | 0 | 33.5 | 34.0 |
| **all** | | 30 | 30 | 0 | 0 | 0 | 29.6 | 44.5 |

p50/p95 are nearest-rank over the runs in each row (n=30 overall). 10 run(s) never reached the agent (person app not frontable, or API billing error before the first call) and are excluded: t7#1, t7#2, t7#3, t7#4, t7#5, t8#1, t8#2, t8#3, t8#4, t8#5.
Agent reported 'failed' (honest failure): 0; no finish / harness error: 0; stale-state refusals by the server: 5.

## Failures

none

Tokens: 2175060 input (incl. cached) / 31770 output over 30 runs.
