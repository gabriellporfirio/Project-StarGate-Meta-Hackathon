# M2 spike (spec section 0)

Date: 1 Nov 2026. Model `muse-spark-1.3` on
`POST https://api.meta.ai/v1/chat/completions`.
Meta calls used: 4 of 5 allowed, each `max_completion_tokens` ≤ 2048,
zero retries needed. No credentials were sent anywhere except the Meta
key to `api.meta.ai` (shown as `***` below). Baseten (item 5) skipped
per the M2 prompt; the product never touches Baseten.

## 1. Tool calling: yes

`finish_reason: tool_calls`, OpenAI shape. Redacted request:

```json
{
  "messages": [
    {"role": "user", "content": "Look up the LineageOS device data for codename enchilada. ..."}
  ],
  "tools": [
    {"type": "function", "function": {
      "name": "lookup_device",
      "description": "Look up LineageOS device data by codename.",
      "parameters": {"type": "object", "properties": {
        "codename": {"type": "string"}}, "required": ["codename"]}}}
  ],
  "tool_choice": "auto",
  "model": "muse-spark-1.3",
  "max_completion_tokens": 1024
}
```
Headers: `Content-Type: application/json`,
`Authorization: Bearer ***`.

Response message shape:

```json
{
  "role": "assistant",
  "content": "I'll look up the device data for codename enchilada.",
  "refusal": null,
  "tool_calls": [
    {"id": "call_...", "type": "function", "function": {
      "name": "lookup_device",
      "arguments": "{\"codename\":\"enchilada\"}"}}
  ]
}
```

With real device data in context, the model twice chose the sensible
next tool on its own (`github_repo` for `LineageOS/
android_device_oneplus_enchilada`, read from the YAML's `tree` field).

## 2. Structured output: yes

`"response_format": {"type": "json_object"}` is accepted;
`finish_reason: stop` with content exactly
`{"device": "OnePlus 6", "codename": "enchilada"}`.

## 3. Tokens and time

| Call | Prompt | Completion (reasoning) | Time |
|---|---|---|---|
| 1 tool probe | 585 tok | 742 (668) | 3.4 s |
| 2 JSON probe | 37 tok | 885 (858) | 3.7 s |
| 3 agent turn, 2.6 KB prompt | 1403 tok | 679 (577) | 2.9 s |
| 4 agent turn, 18 KB prompt | 5158 tok | 720 (619) | 3.2 s |

Reasoning dominates every call (~600-860 tokens). At ~700-900
completion tokens per call, the 20,000-token run budget allows 20+
calls, so the 12-call cap binds first. Whole-run time is fine:
~12 calls × ~4 s ≈ 50 s of model time inside the 180 s budget.

## 4. Source URLs and field names

`lineage_device`:
`https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/<codename>.yml`
(200, `text/plain`). enchilada (1710 bytes) has: `codename`,
`current_branch: 22.2`, `versions: [16.0 … 22.2]`, `maintainers:
[LuK1337]`, `tree: android_device_oneplus_enchilada`, `kernel: {repo:
android_kernel_oneplus_sdm845, version: '4.9'}`. Support meaning is
mirrored exactly from the wiki's own rule: `_includes/templates/
device_info.md:7-11` (LineageOS/lineage_wiki, observed 1 Nov 2026)
shows the "no longer maintained" warning when `maintainers` is empty
(Liquid `empty`: missing, null or `[]`), and `_includes/
deviceinfo.html:296-302` treats `current_branch` as current only when
maintainers is non-empty — so `current_branch` alone never means
current. Mirrored mapping: maintainers non-empty → `current`;
maintainers empty → `historical_only` (discontinued; `versions` are
the historical builds); YAML 404 → `not_listed`; unreadable →
`unknown` with reason. The `tree` and `kernel.repo` fields are the
authoritative structured links for `repos.device_tree` /
`repos.kernel` (the kernel is chip-family-named, no codename — shared
repos count). There is NO vendor-repo link field (`vendor` /
`vendor_short` are manufacturer names), so `repos.vendor` has no
established-link source from this file.

`github_repo`: `https://api.github.com/repos/<owner>/<repo>` (200).
Fields: `full_name`, `default_branch` (e.g. `lineage-24.0`),
`pushed_at`, `archived`. Rate-limit headers `x-ratelimit-limit` /
`-remaining` / `-reset` present (unauthenticated core quota 60/hr).

`github_search`:
`https://api.github.com/search/repositories?q=…&per_page=…` (200).
Fields: `total_count`, `items[].full_name`. Search quota 10/min,
shared. `q=enchilada in:name` returns 367 hits, mostly junk (food
repos) — a name match proves nothing, per spec.

`hf_model_search`: `https://huggingface.co/api/models?search=…&author=…`
(200). Returns an array of model objects (`id`, `likes`, …);
`author=meta-llama&search=llama` returns 70 hits. No `sha` here.

`hf_model`: `https://huggingface.co/api/models/<repo_id>` (200, even
for gated `meta-llama` repos, no token). Fields: `id`, `author`,
`sha` (revision pin for the report), `license` (top-level, often
null), `cardData.license` (e.g. `llama3.2`), `tags` (`base_model:*`
marks derivatives; absent here), `siblings[].rfilename` (names only,
NO sizes), `gated`.

File sizes: `https://huggingface.co/api/models/<repo_id>/tree/<rev>`
(200). Entries: `{type, oid, size, path}`. This is the explicit
file-metadata request; pin `<rev>` to the `sha` from `hf_model`.

`pmos_page`: the rename is CONFIRMED, not assumed.
`https://wiki.postmarketos.org/wiki/OnePlus_6_(oneplus-enchilada)`
returns 301 to
`https://wiki.nura.eco/wiki/OnePlus_6_(oneplus-enchilada)` (200,
HTML, `<title>OnePlus 6 (oneplus-enchilada) - Nura Wiki</title>`).
Host for M2: `wiki.nura.eco`. Title match rule: the page title must
contain the codename, else `pmos.status: unknown`.

## Fetch allowlist (confirmed)

| Tool | Host | Route |
|---|---|---|
| `lineage_device` | `raw.githubusercontent.com` | `/LineageOS/lineage_wiki/main/_data/devices/<codename>.yml` |
| `github_repo` | `api.github.com` | `/repos/<owner>/<repo>` |
| `github_search` | `api.github.com` | `/search/repositories?...` |
| `hf_model_search` | `huggingface.co` | `/api/models?...` |
| `hf_model` | `huggingface.co` | `/api/models/<repo_id>` |
| `hf_model_files` | `huggingface.co` | `/api/models/<repo_id>/tree/<rev>` (file sizes) |
| `pmos_page` | `wiki.nura.eco` | `/wiki/<Device>_(<vendor>-<codename>)` |

`wiki.postmarketos.org` is not on the list: it 301-redirects to
`wiki.nura.eco`, and each redirect target is re-checked against this
table. HTTPS GET only, no credentials on any of these hosts.

## Chosen mode: agent

Tool calling works, structured output works as a backstop, and the
model picks sensible next tools from real data. M2 builds agent mode:
the model chooses the next allowed tool and code validates and runs
it. Pipeline mode is not built.
