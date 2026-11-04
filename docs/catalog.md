# Model catalog (M3)

`catalog/models.json` (schema `catalog-v1`, validated by
`stargate/catalog.py`) lists candidate on-device models for the
Assistant. Each entry:

- `id`, `source` (Hub repo), `revision`, `files` (weight files with
  whole-byte sizes), `format`, `runtime`, `abi`, `licence`. Any of
  these except `id` and `files` may be `null` (unknown); a missing
  value never counts as "too big".
- `files` lists the weight file(s) only. Tokenizer, config and
  training files exist upstream but are excluded as negligible next
  to the weights; the fit engine multiplies the listed bytes by the
  runtime factor anyway. M4 file-set exception: the ExecuTorch entry
  lists its `.pte` weight file AND `tokenizer.model`, because M4
  spec section 5a binds the assistant manifest's file set (names +
  sizes) to the handoff's, and the tokenizer is device payload with
  its own expected hash. The fit engine sums both either way.
- `provenance`: each entry carries its own. `sourced` entries cite
  the official model page URL and the fetch date; `synthetic`
  entries are test-only and labelled so. Real entries are never
  labelled synthetic.
- `estimates`: the value fields that are planning estimates rather
  than sourced (for the shipped entries: `runtime` and `abi`). The
  loader requires this list and rejects unknown field names, and
  the tests check that every non-null value is either sourced,
  listed here, or (when null) unknown. A field listed here cannot
  also be a sourced field (`source`, `revision`, `format`,
  `licence`): the loader rejects such entries (M4 section 7).

Field provenance for the three shipped entries (all fetched 1 Nov 2026;
two M3 SmolLM2 entries plus the M4b ExecuTorch entry):

| Field | Value source |
|---|---|
| `files[].bytes` | Exact sizes from the Hub API tree for each repo: 269060552 and 723674912 bytes (SmolLM2, `.../tree/main`), and 1135951488 + 2183982 bytes (ExecuTorch `.pte` + tokenizer, `.../tree/911221e0…` LFS sizes) |
| `format` | The library tag on each model's page: `Safetensors` for SmolLM2 (original published weights, not a phone-ready build, M4 picks the real file); the ExecuTorch export description for the `.pte` entry |
| `licence` | `apache-2.0` (SmolLM2 pages) and `llama3.2` (ExecuTorch community repo page) |
| `runtime` | Planning estimate for all three (see below) |
| `abi` | Planning estimate for all three (see below) |

## Why runtime and abi stay estimates on the verified entry

The catalog schema and `test_every_value_sourced_estimated_or_unknown`
only allow `source`, `revision`, `format` and `licence` as sourced
fields: anything else non-null must be listed in `estimates`. M4b
verified the ExecuTorch entry's runtime (1.5.1 AAR contents, minSdk,
`LlmModule` in `classes.jar`) and ABI (`arm64-v8a` `.so` in the AAR),
but "sourced" in this schema means "read from the official model
page", and runtime/ABI are not model-page facts — so they stay
labelled estimates even though the evidence is stronger than a guess.
The verified values live in `assistant-manifest.json` and
`docs/m4-assistant.md` instead.

## Why SmolLM2 and not a Meta Llama model

Honestly: no Meta Llama model was evaluated during M3, so there is
no recorded finding against Llama (no gated repo was hit, no
licence was refused — Llama simply wasn't tried). M2's live run
returned no AI candidates at all (`ai.candidates` came back
`unknown (not_fetched)`; see `docs/m2-live-check.md`), so M3 picked
two small instruct models that were easy to verify: exact byte
sizes on ungated Hugging Face pages, `apache-2.0` licences, and
`safetensors` weights. Size also mattered: the 135M/360M weights
(269 MB / 724 MB) let the budget phone refuse on RAM while the
flagship fits, which is what the M3 refusal tests needed.

For the demo, M4 will prefer a Meta model; this is an open question.

## Open question: Muse Glimmer

Whether Muse Glimmer can be a catalog entry is still an open
question, and its values stay unknown until checked. It is left OUT
of `catalog/models.json` for now: with all values unknown it would
turn definite refusals into "unconfirmed".
