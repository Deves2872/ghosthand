# Benchmark results — 20261124-044330

Model `claude-opus-5-5`, effort `medium`, timeout 300.0s per run, 5 runs per task from the same starting state. Outcomes checked on disk / in the apps, independently of the agent's message.

| task | level | runs | pass | timeout | said done but wasn't | focus moved | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|
| t8 Background edit+save while a person types in Stickies | hard | 5 | 5 | 0 | 0 | 0 | 11.9 | 12.6 |
| **all** | | 5 | 5 | 0 | 0 | 0 | 11.9 | 12.6 |

p50/p95 are nearest-rank over the runs in each row (n=5 overall).
Agent reported 'failed' (honest failure): 0; no finish / harness error: 0; stale-state refusals by the server: 0.

## Failures

none

Tokens: 107942 input (incl. cached) / 2342 output over 5 runs.
