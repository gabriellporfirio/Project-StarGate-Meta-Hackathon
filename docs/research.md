# M2 research agent

`stargate research` researches one phone with Meta and writes a cited
`research-v1` report. Agent mode only (chosen by the spike in
`docs/m2-spike.md`): the model picks the next allowlisted tool and
deterministic code validates the call, runs the fetch and extracts
every fact. Model prose is display-only and never used as a fact.

## Commands

- `stargate research --codename enchilada --device "OnePlus 6"`
- `stargate research --sim budget` (simulated identity, offline profile)
- `--json` prints the report as JSON; `--json-out PATH` also writes it.
- `--record PATH` saves the trace; `--replay PATH` rebuilds the report
  from a trace with no network at all.
- `--no-cache` bypasses the fetch cache.
- `python -m stargate.research validate <file> [...]` checks reports.

Exit codes: 0 complete, 4 partial (a budget or deadline ran out),
2 missing Meta key, 5 Meta failure (the M0 result code is shown),
6 invalid input.

## Fetch allowlist

HTTPS GET only, no credentials on any of these hosts. Every redirect
target is re-checked against this table (at most 3 hops); anything
else is refused before any request.

| Tool | Host | Route |
|---|---|---|
| `lineage_device` | `raw.githubusercontent.com` | `/LineageOS/lineage_wiki/main/_data/devices/<codename>.yml` |
| `github_repo` | `api.github.com` | `/repos/<owner>/<repo>` |
| `github_search` | `api.github.com` | `/search/repositories?...` |
| `hf_model_search` | `huggingface.co` | `/api/models?...` |
| `hf_model` | `huggingface.co` | `/api/models/<repo_id>` |
| `hf_model_files` | `huggingface.co` | `/api/models/<repo_id>/tree/<rev>` (file sizes) |
| `pmos_page` | `wiki.nura.eco` | `/wiki/<Device>_(<vendor>-<codename>)` |

`wiki.postmarketos.org` 301-redirects to `wiki.nura.eco`, which is why
only the new host is listed. Fetch outcomes: `ok`, `denied`,
`rate_limited`, `timeout` (`too_large` over 1 MiB), `refused`,
`error`. A 404 is `ok`: a definitive absence the fact layer reads as
`not_listed` / `not_found` / `none_found`.

## Run budgets

Meta: 12 calls (attempts included), 2048 completion tokens per call,
20,000 total, 24,000 source-text chars per call, 60,000 total.
HTTP: 30 requests; GitHub 10 core + 5 search. Whole run: 180 s.
Code checks before every request (retries included) and lowers
`max_completion_tokens` to the remaining allowance; under 256 left,
the run stops. When a budget runs out the report is marked `partial`
(exit 4), except a low GitHub allowance, which only stops GitHub
calls. Fetch bodies are cached in memory for 24 hours.

## Report layers

1. Fetch records (every request: tool, URLs, status, time, size,
   SHA-256, outcome).
2. Facts `F1..F9`: `lineage.support`, `repos.device_tree`,
   `repos.kernel`, `repos.vendor`, `repos.candidates`, `ai.candidates`,
   `pmos.status`, `stock_firmware`, `treble_gsi`; each `found`,
   `none_found` or `unknown` with a reason, subject codename and the
   fetch IDs it came from.
3. Model notes, labelled `model explanation, not checked`, with cited
   fact IDs checked by code.

Support rule (mirrored from the wiki itself): maintainers non-empty
means `current`; empty means `historical_only`; a missing YAML means
`not_listed`. See `docs/m2-spike.md` for the citation. AI model
entries carry the model revision and per-file sizes from an explicit
file-metadata request. Stock firmware and Treble GSI are
`not_covered` in M2.

## Privacy

Only manufacturer, model, codename, Android release, SDK level and
platform leave the machine, and only inside Meta prompts. Fetched
pages are untrusted data. The Meta key goes only to `api.meta.ai`
and is stored redacted. `stargate doctor` (the M0 developer check)
may report whether `BASETEN_API_KEY` is set, but it never sends the
value anywhere. The product never calls Baseten, and the research
path never reads `BASETEN_API_KEY` at all.
