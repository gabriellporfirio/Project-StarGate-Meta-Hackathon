# M0 live check — 30 Oct 2026

## Live run: `.venv/bin/stargate doctor` → exit 0

- Endpoint: `POST https://api.meta.ai/v1/chat/completions`
- Model requested: `muse-spark-1.3`
- Model returned: `muse-spark-1.3`
- Attempts: 1, 1810 ms; `Meta inference: ok`, `Result: ok`
- Plan pin verified: `b498e70...` HEAD match, clean tree
- Report-only: git 2.50.1, 27.4 GB free, Baseten not configured

## Model IDs for this key (`GET /v1/models`, same day, http 200)

`muse-wonder-1.0`, `sam-3.1`, `muse-spark-1.3-contributor`,
`muse-voice-transcribe-1.0`, `muse-spark-1.3`, `muse-image-1.0`,
`muse-spark-1.2-contributor`, `muse-spark-1.2`, `muse-spark-1.1`

Picked `muse-spark-1.3` (Meta's recommended Spark, plain ID).

## Missing-key run: `env -u MODEL_API_KEY .venv/bin/stargate doctor` → exit 2

`Meta inference: missing-credential` (0 attempts, no request sent),
`Result: missing-credential`.

## Offline tests: `.venv/bin/python -m unittest -v` → exit 0

36 tests, all pass.

## Request shape (recorded values)

20 s timeout, 1 retry, `max_completion_tokens = 256`. 64 was too small:
Spark 1.3 spent 62 reasoning tokens on the probe and returned
`content: null` (`finish_reason: length`); the strict parser correctly
reported `bad-response` until the cap was raised.

## Path to green (honest log)

1. First live run crashed in report formatting (a duplicated tuple entry
   landing on `%d`); fixed, covered by the committed failure-branch tests.
2. Second run: `bad-response`, empty answer — the 64-token cap vs reasoning
   (diagnosed from the raw response, cap raised to 256).
3. Third run: `ok` as recorded above.

## Follow-up: cap raised to 1024 (same day)

A later re-run at cap 256 failed again (`bad-response`,
`finish_reason=length`): reasoning spend varies per call. Cap raised to
1024; doctor now reports token usage on the ok line.

3 consecutive live runs, all `ok`, exit 0, 1 attempt each,
model requested/returned `muse-spark-1.3`:

| Run | Result | Time | Completion tokens | Reasoning tokens |
|---|---|---|---|---|
| 1 | ok | 1902 ms | 171 | 161 |
| 2 | ok | 2156 ms | 226 | 216 |
| 3 | ok | 2049 ms | 243 | 233 |

Observed reasoning range on the fixed trivial probe: 161–233 tokens.
