# M2 live check

Command (exit 0):

```
.venv/bin/stargate research --codename enchilada --device "OnePlus 6" --json-out /tmp/r.json --record /tmp/r.trace
```

- Report: `/tmp/r.json` (valid `research-v1`, `provenance: live`).
- Trace: `/tmp/r.trace` (model requests/responses redacted, tool
  calls/responses, final report). Replay with
  `stargate research --replay /tmp/r.trace` (exit 0, no network).
- Budgets used: 5 Meta calls (3103 completion / 20644 prompt tokens),
  4 HTTP fetches (2 GitHub core, 0 search), ~20 s wall time.

Note: a first live attempt exited 5 (`bad-response`, one empty
`finish_reason=length` reply). The client now retries an empty reply
once; the retry path is covered by tests. No record was written for
the failed attempt.

## Live output (stdout)

```
Research report (live): OnePlus 6 [enchilada]
State: complete

LineageOS support:
  support: current (versions: 16.0, 17.1, 18.1, 19.1, 20, 21, 22.1, 22.2; maintainers: LuK1337) [F1 found] <https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/enchilada.yml>

Repos:
  Device tree: LineageOS/android_device_oneplus_enchilada (branch lineage-24.0)
    [F2 found] <https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/enchilada.yml>
  Kernel: LineageOS/android_kernel_oneplus_sdm845 (branch lineage-22.2)
    [F3 found] <https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/enchilada.yml>
  Vendor: none found [F4 none_found] <https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/enchilada.yml>
  Candidates: unknown (not_fetched) [F5 unknown (not_fetched)]

AI models (Meta Llama, as the publisher states them):
  Models: unknown (not_fetched) [F6 unknown (not_fetched)]

postmarketOS:
  page: listed (OnePlus 6 (oneplus-enchilada) - Nura Wiki) [F7 found] <https://wiki.nura.eco/wiki/OnePlus_6_(oneplus-enchilada)>

Not covered in M2: stock firmware, Treble GSI.

Model explanation (model explanation, not checked):
(see /tmp/r.json notes; display-only, not checked)
```
