# Decisions (M0)

## Plan pin

- Approved plan commit: `b498e7037e1e064fd6db8ff73687ecc5a81e449b`
  (`gabriellporfirio/Project-Stargate`, M0 spec v3.1 Part B).
- Cloned read-only to `/workspaces/Project-Stargate`, detached HEAD at the
  pin, origin and clean tree verified before any product work.
- `stargate doctor` re-verifies the pin on every run (HEAD must match and the
  tree must be clean). Override the plan-repo path with `STARGATE_PLAN_REPO`.

## Meta API (checked 30 Oct 2026)

- Endpoint: `POST https://api.meta.ai/v1/chat/completions`
  (OpenAI-compatible Chat Completions surface).
- Auth: `Authorization: Bearer $MODEL_API_KEY`, key from the environment only.
- Model: `META_MODEL = muse-spark-1.3` (Meta's recommended Spark; verified
  present in the live `GET /v1/models` list on 30 Oct 2026 — full ID list in
  `docs/m0-live-check.md`). Requested/returned IDs are recorded per run.
- Output cap: `max_completion_tokens = 1024` (Meta's documented parameter
  name for this surface; value recorded here). 64 was too small (62 reasoning
  tokens on the trivial probe, `content: null`, `finish_reason: length`).
  256 then proved flaky: reasoning spend varies run to run (observed
  161–233 tokens), and a later identical run hit the cap again. 1024 is
  still small (<1% of the 131072 max output) with ~4x headroom over the
  observed max. Verified by 3 consecutive live `ok` runs; per-run token
  counts are in `docs/m0-live-check.md`.
- Doctor reports the response's `usage` block on the ok line
  (`N completion tokens (M reasoning)`) when the API includes it.
- Limits: 20 s timeout per attempt, 1 retry (2 attempts max) with a 1 s pause,
  retried only on `network-error` and `quota-or-rate-limited`.
- Inference sends exactly one request per attempt; listing models is a
  separate discovery step and never counts as the inference proof.

Doc URLs used:

- <https://dev.meta.ai/docs/protocols/chat-completions> (chat-completions reference)
- <https://dev.meta.ai/docs/getting-started/overview> (Model API overview)
- <https://dev.meta.ai/> (docs home; API keys come from the Model API dashboard)
- <https://ai.developer.meta.com/docs/llms.txt> (docs index mirror)
- <https://github.com/meta-models/meta-model-cookbook> (official cookbook;
  quickstart is chat completions via the OpenAI SDK pointed at the Meta base URL)
- Corroboration: three surfaces (Responses, Chat Completions, Messages) share
  models, auth and price; base URL `https://api.meta.ai/v1` everywhere.

## Status mapping and exit codes

| Signal | Result code | Exit |
|---|---|---|
| valid non-empty answer | `ok` | 0 |
| `MODEL_API_KEY` unset/empty (no request sent) | `missing-credential` | 2 |
| HTTP 401/403 | `auth-failed` | 3 |
| HTTP 429/402 | `quota-or-rate-limited` | 4 |
| timeout/DNS/refused/reset/TLS, HTTP 408, HTTP 5xx | `network-error` | 5 |
| HTTP 400, other unexpected status, invalid/empty 2xx body | `bad-response` | 6 |
| HTTP 404 | `model-not-available` | 7 |
| interpreter below 3.12 | `interpreter-unsupported` | 20 |
| plan checkout mismatch/dirty/missing | `plan-pin-failed` | 21 |

5xx maps to `network-error` (server-side, transient, worth the one retry);
400 maps to `bad-response` (our request was rejected, retrying is pointless).

## Report-only checks

- `git --version` and free disk at `/` are reported and never fail the run.
- Baseten appears only when both `BASETEN_API_KEY` and `BASETEN_MODEL` are
  set; M0 reports "configured" without probing it (no extra network).

## Build

- `pyproject.toml` uses the `setuptools>=64` build backend (PEP 660 editable
  installs). Whatever pip downloads into the isolated build environment is
  transcribed into `INSTALL-LOG.md` from pip's actual output.
- `.gitignore` covers `.venv/` and `__pycache__/` so the checkpoint commit
  stays clean. Hygiene, not a feature.
