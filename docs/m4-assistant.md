# M4b step 0d: Assistant runtime, model, and payload decision

Status: M4c part 1 applied, offline (two variants, three
modes, pinned providers, Keystore keys, ledger + gate; fixup 4:
fresh context per question, app device-fact answers; fixup 3:
Route B folder creation with per-check errors, Llama 3.2 chat
format and stop tokens, guarded Mac steps from the proof run,
cleaner screen; fixup 2: licence sources, zsh-safe Mac steps,
relative paths; fixup 1: notices, Mac steps, APK out of git,
deps in manifest, catalog doc). No installs. Spec:
`plan/milestones/M4-image-build.md` at merged commit
`128e062535359808a89466cdc42df632359d4b99`, sections 4, 5a, 11.
M3 base: `e701acd` in this worktree. Fetch log: `docs/m4-sources.md`
(20 step-0 + 8 step-0b + 3 step-0c + 1 step-0d = 32 used, 1 Nov 2026).
Step 0d downloaded both pairs (5 files, bytes + SHA-256 all match
published values), settled minSdk (23) and the LLM class
(`LlmModule`) from the AAR, listed the llama.cpp tarball, and
recommends Pair E on a 6 GB emulator. Step 1A added the catalog
entry and re-ran the plan (flagship, fits, emulator_only).

## 1. Runtimes for arm64 Android

### ExecuTorch 1.5.1 (Meta/PyTorch)

- Exact version: 1.5.1. Source: [releases](https://github.com/pytorch/executorch/releases)
  (Latest tag v1.5.1) and Maven listing showing 1.5.1 as newest
  (2026-10-01).
- Source URL: https://github.com/pytorch/executorch
- Licence: BSD License, Copyright Meta Platforms, Inc. and affiliates
  ([LICENSE](https://github.com/pytorch/executorch/blob/main/LICENSE)).
- Prebuilt Android library: Maven `org.pytorch:executorch-android:1.5.1`
  (AAR). Per the [Android doc](https://docs.pytorch.org/executorch/stable/using-executorch-android.html),
  every release uploads a prebuilt AAR to Maven and S3; no NDK build needed.
  Size: `executorch-android-1.5.1.aar` = **7584141 bytes**, SHA-256
  `d67d39eb51e00c996162e1d9898519a622918b20e4e18ea479f5fee4fe920e72`
  (Maven `.sha256` file; download verified to match). ABIs in the AAR:
  arm64-v8a and x86_64 — confirmed in the download (`jni/arm64-v8a/` and
  `jni/x86_64/` each carry `libexecutorch.so`).
- Supported model formats: ExecuTorch `.pte` (doc loads `/data/local/tmp/add.pte`
  and pushes a `.pte` to the device; LLaMA use case is explicitly included).
- Minimum Android version: **minSdkVersion 23 (Android 6.0)**, read from
  the downloaded AAR's `AndroidManifest.xml` (`<uses-sdk
  android:minSdkVersion="23" />`). The emulator image is API 34, so 23
  is satisfied with margin. Settles the step-0c open point; no Gradle
  fetch needed.
- File-path loading: **yes**. `Module.load("/data/local/tmp/add.pte")` loads
  from an absolute file path, not only from APK assets (same doc).
- App integration route: **Java API in the AAR, confirmed**. `classes.jar`
  in the download contains `org.pytorch.executorch.extension.llm.LlmModule`
  plus `LlmGenerationConfig`, `LlmModuleConfig`, and `LlmCallback`.
  No network permission needed; no child process.
- Runtime dependencies (POM + offline reference scan of the 60 AAR
  classes, step 1C): **kotlin-stdlib 1.9.23** (kotlin/* in 60/60
  classes — compile and runtime), **fbjni 0.7.0** (`com/facebook/jni/*`
  in 18 classes incl. `Module`, plus `NEEDED libfbjni.so` in both
  shipped `.so` files), **soloader nativeloader 0.10.5**
  (`com/facebook/soloader/*` in 4 classes incl. `Module`).
  **core-ktx 1.13.1 is NOT needed** (zero `androidx/*` references).
  Owner-approved in step 1C and verified: kotlin-stdlib (1718956
  bytes, no published hash, computed SHA-256
  `8910cc238807d86ef550cb1f0b10dd5ed40b35a4ec1a52525f760aede84ead37`),
  fbjni AAR (4349156 bytes, published hash matched), nativeloader
  (3158 bytes, published hash matched). All in `/workspaces/m4b-tools/`.

### llama.cpp b11721 (ggml-org, settled build)

- Exact version: b11721 (pre-release, 18 Oct 2026, commit
  `ff5a23376725e5a889fa0bfb987b7541dc304da0`). Newest release
  published at least 14 days before 1 Nov 2026 with an Android arm64
  asset (releases API, fetches 23/25/26). Replaces the one-day-old
  b12024 from step 0. Source:
  [releases](https://github.com/ggml-org/llama.cpp/releases).
- Source URL: https://github.com/ggml-org/llama.cpp
- Licence: MIT, Copyright (c) 2023-2026 The ggml authors
  ([LICENSE](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE)).
- Prebuilt Android library: release file
  `llama-b11721-bin-android-arm64.tar.gz` (Android arm64 CPU build).
  URL: https://github.com/ggml-org/llama.cpp/releases/download/b11721/llama-b11721-bin-android-arm64.tar.gz
  Size: **76297099 bytes exact** (releases API), listed as 72.8 MB,
  with published SHA-256
  `94502b60b23f1cbc651e7d9720dacdc5b974686f9a61cf337d0611c1c1b9850f`
  (expanded assets page; download verified to match). Tarball contents
  (listed, nothing run): 44 members under `llama-b11721/` — 21
  binaries including `llama-cli` and `llama-server` (small ~7 KB
  stubs) plus `llama`, `llama-bench`, `llama-quantize`,
  `llama-tokenize`, multimodal CLIs and tools; 21 shared libraries
  (`libllama.so`, `libllama-common.so`, `libllama-server-impl.so`,
  `libllama-cli-impl.so`, `libggml*.so`, `libmtmd.so`); LICENSE.
  293836806 bytes uncompressed (~280 MiB).
  This is a CLI/binary tarball, not an AAR; it avoids an NDK build
  for command-line inference. No prebuilt AAR was found in the
  fetched release assets; in-app JNI use would need an NDK build
  (not proposed here; the demo path is the prebuilt CLI).
- Supported model formats: `.gguf` (Android doc uses `{model}.gguf`
  throughout and parses GGUF metadata).
- Minimum Android version: **android-28 (Android 9)** per the official
  cross-compile example (`-DANDROID_PLATFORM=android-28`) in the
  [Android doc](https://github.com/ggml-org/llama.cpp/blob/master/docs/android.md).
  No separate minSdk statement was found; 28 is the only official number,
  treated as the minimum for this decision.
- File-path loading: **yes**. CLI `llama-cli -m ~/{model}.gguf` takes a file
  path; the Android binding loads "via its app-private file path" and the
  doc pushes `{model}.gguf` with adb (same doc).
- App integration route: **child process** (`llama-cli` started by the app).
  The Assistant app has NO network permission, so it cannot talk to a
  local server over a socket, even on loopback. The realistic route is
  packaging the `llama-cli` binary inside the APK as a native library
  (`lib*.so` in `jniLibs`, `extractNativeLibs=true`), starting it from
  the app's `nativeLibraryDir`, and passing the model path from spec
  section 4b (`-m <path>`). This packaging route is **not documented
  in any fetched page; to be proven in M4b step 1**.

## 2. Models

### (a) Muse Glimmer (answers master-plan Q15)

- What it is: per master-plan Q15, a Meta open-weight model listed in the
  hackathon participant guide (owner-supplied PDF; event pages block
  automated reading). A project using it on its own hardware is eligible
  for the $10K open-weight bonus.
- Size, licence, formats: **unknown**. Two official-page fetches
  (`huggingface.co/meta-llama/Muse-Glimmer` page and API tree) both
  returned HTTP 401, which proves only "no public metadata", not
  existence, gating, or absence.
- Loadable by the runtimes above on Android: **no (not workable)**. No
  model file, format, or size is known, so neither runtime can be shown
  to load it. Q15's answer for M4: Glimmer cannot be the Assistant model;
  the bonus path is closed unless the owner supplies a file and licence.
- Step 0b: Muse Glimmer was not reachable (HTTP 401), so Q15 stays
  open. The hackathon's open-weight bonus counts only Muse Glimmer
  or Muse Spark Open, so a Llama Assistant would not qualify for it
  on its own.

### (b) Smallest Meta Llama instruct models with an on-device format

The smallest Llama 3.2 instruct model is the 1B variant.

**Upstream (not downloadable within limits):**

- Repo: `meta-llama/Llama-3.2-1B-Instruct`, revision `main`.
- Licence: `llama3.2` (Llama 3.2 Community Licence; page shows "I Accept").
- Gated: metadata publicly readable (page + API tree returned 200 on
  1 Nov 2026), but the licence requires acceptance; download-time gating
  to be confirmed if ever used. Not used here.
- Files: `model.safetensors` = **2471645608 bytes** (~2.47 GB), which
  exceeds the 2 GB model-download cap, plus tokenizer/config files.
  Verdict: original weights unusable for M4.

**On-device ready-made (usable):**

- Repo: `bartowski/Llama-3.2-1B-Instruct-GGUF`, revision
  `067b946cf014b7c697f3654f621d577a3e3afd1c` (pinned 1 Nov 2026;
  `main` is a branch, not a revision). Community quant of the Meta
  model above (author `bartowski`, base `meta-llama`), **not
  published by Meta**; GGUF library tag.
- Licence: `llama3.2` (same Llama licence; page shows "License: llama3.2").
  The licence still applies to these converted files, including its
  attribution requirement: keep the notice "Llama 3.2 is licensed
  under the Llama 3.2 Community License, Copyright (c) Meta
  Platforms, Inc. All Rights Reserved." with distributed copies
  (wording as recorded from the fetched licence page in step 0).
- Gated: **no**. Page, revision API, and API tree all returned 200
  without credentials. No Hugging Face acceptance step needed.
- Files at the pinned commit (single-file GGUF; tokenizer embedded,
  no separate download; SHA-256 from the tree API `lfs` entries):
  - `Llama-3.2-1B-Instruct-Q4_K_M.gguf` = **807694464 bytes**,
    SHA-256 `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
    (primary).
  - `Llama-3.2-1B-Instruct-IQ3_M.gguf` = **657289344 bytes**,
    SHA-256 `c80cc062a721c267ec50fee83fe6b55d36fc7abe708392dbe22e16fbd42687e8`
    (smaller alt).
  - Others at the pin: Q4_K_S 775647360, Q4_0 773025920,
    Q3_K_L 732524672, IQ4_XS 743141504, Q5_K_M 911503488,
    Q6_K 1021800576, Q8_0 1321083008, f16 2479595360 (exceeds cap),
    plus .imatrix and more (same tree fetch).
- Note: `ggml-org/Llama-3.2-1B-Instruct-Q8_0-GGUF` (official GGUF) returned
  401 for page and API tree, so no bytes available; not used.

### (c) M3 SmolLM2 entries (fallback)

Existing catalog entries (sourced 1 Nov 2026, M3):

- `smollm2-135m-instruct`: HuggingFaceTB/SmolLM2-135M-Instruct, rev main,
  `model.safetensors` 269060552 bytes, apache-2.0, safetensors original
  weights, "not a phone-ready build".
- `smollm2-360m-instruct`: HuggingFaceTB/SmolLM2-360M-Instruct, rev main,
  `model.safetensors` 723674912 bytes, apache-2.0, same format note.

Ready-made GGUF exists for the 135M (bartowski/SmolLM2-135M-Instruct-GGUF,
rev main, ungated): Q4_K_M = **105454432 bytes**, Q2_K = 88202080 bytes
(smallest), Q8_0 = 144811360, f16 = 270885952. Quant-page licence was not
fetched (budget); base licence is apache-2.0. Fallback pair documented in
section 3 but not recommended (licence page unconfirmed; primary is Meta).

### (d) ExecuTorch ready-made `.pte` (Pair E model, recommended)

`executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET` at
`911221e0f49d3354cbd4697a9c09feee25cadec7`, licence llama3.2, ungated.
`.pte` (1135951488 bytes) + `tokenizer.model` (2183982 bytes), both
SHA-256 published and downloads verified. Full table in section 3.

## 3. Complete payload per workable pair

### Pair R (alternative, fully verified): llama.cpp b11721 + Llama 3.2 1B Q4_K_M GGUF (pinned)

Every file the runtime needs. GGUF is single-file (model + tokenizer
embedded); llama.cpp loads it with `-m <path>`; no config/support files.
Called as a child process (section 1). Downloads verified; kept as the
documented alternative now that Pair E is complete and recommended.

| File | Bytes | SHA-256 | Source revision |
|---|---|---|---|
| `Llama-3.2-1B-Instruct-Q4_K_M.gguf` | 807694464 | 6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83 | bartowski/Llama-3.2-1B-Instruct-GGUF @ 067b946cf014b7c697f3654f621d577a3e3afd1c |

- Ready-made: **yes**. No conversion.
- Conversion tool: none needed.

Smaller alternative in the same repo (same licence, same single-file
shape, same pin): `Llama-3.2-1B-Instruct-IQ3_M.gguf`, 657289344 bytes,
SHA-256 `c80cc062a721c267ec50fee83fe6b55d36fc7abe708392dbe22e16fbd42687e8`.
Kept as a backup file, not a separate recommendation.

### Pair F (fallback, not recommended): llama.cpp b11721 + SmolLM2 135M Q4_K_M GGUF

| File | Bytes | SHA-256 | Source revision |
|---|---|---|---|
| `SmolLM2-135M-Instruct-Q4_K_M.gguf` | 105454432 | unknown, computed after download | bartowski/SmolLM2-135M-Instruct-GGUF @ main (unpinned) |

- Ready-made: **yes**. No conversion.
- Not recommended because the quant repo's licence page was not fetched
  within budget (base model is apache-2.0), its revision is unpinned,
  and the spec prefers Meta. Runtime updated to the settled b11721
  for consistency; pinning this model would need two more fetches.

### Pair E (recommended): ExecuTorch 1.5.1 AAR + ready-made `.pte` (pinned, verified)

Step 0b found the ready-made file in `executorch-community`; step 0c
recorded every file's bytes and SHA-256 at the pin; step 0d verified
all downloads and settled minSdk (23) and the LLM class:

- Repo: `executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET`,
  revision `911221e0f49d3354cbd4697a9c09feee25cadec7` (pinned 1 Nov 2026).
- Licence: `llama3.2`. Gated: **no** (revision API returned 200).
- Device payload (model total **1138135470 bytes**):

| File | Bytes | SHA-256 |
|---|---|---|
| `Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte` | 1135951488 | 8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b |
| `tokenizer.model` | 2183982 | 82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55 |

- Export-time artifacts at the pin (not device payload): `config.json`
  (27 bytes), `params.json` (273 bytes), export notebook (83165 bytes),
  `README.md` (40241 bytes), `.gitattributes` (1600 bytes); all small
  non-LFS files, SHA-256 unknown, computed after download.
- Unexamined alternatives in the same org (same licence tag, public):
  `Llama-3.2-1B-Instruct-QLORA_INT4_EO8-ET` and
  `Llama-3.2-1B-Instruct-ET`.
- Called through the AAR's Java API (section 1):
  `org.pytorch.executorch.extension.llm.LlmModule`, confirmed in the
  downloaded `classes.jar`.
- Runtime: `executorch-android-1.5.1.aar`, 7584141 bytes, SHA-256
  `d67d39eb51e00c996162e1d9898519a622918b20e4e18ea479f5fee4fe920e72`
  (Maven `.sha256`; download verified).

Complete: every needed value is known and every download verified.
Meta's own runtime, a real Android library, a clean Java-API route,
and a pinned ready-made `.pte` (no export tool needed).

## 4. Totals, RAM, and fit (4 GB emulator)

Method: `docs/fit-budgets.md`. Memory = 1610612736 (OS) + 67108864 (Files,
if selected) + 134217728 (Assistant) + model bytes x2. Storage = 209715200
(Files, if selected) + 157286400 (Assistant app) + model bytes. Emulator RAM
4 GB = 4294967296 bytes. Model cap 2 GB = 2147483648 bytes.

**Pair R (807694464-byte model, 76297099-byte runtime tarball):**

- Model download: 807694464 bytes (~770 MiB). Under 2 GB. Pass.
- Total download (model + runtime tarball): 807694464 + 76297099
  = **883991563 bytes** (~843 MiB combined, both exact). Under the
  10 GB cross-worker cap.
- RAM Files+Assistant: 1610612736 + 67108864 + 134217728 + 1615388928
  = **3427328256 bytes** (~3.19 GiB). Headroom to 4 GB: 867639040 bytes
  (~827 MiB). Fits with room for Android.
- RAM Assistant-only: 3360219392 bytes (~3.13 GiB). Headroom ~891 MiB. Fits.
- Storage Files+Assistant: 209715200 + 157286400 + 807694464
  = 1174696064 bytes (~1.09 GiB). AVD model area per spec 4b (model +
  no extracted copy + 1 GB headroom): 807694464 + 1073741824
  = 1881436288 bytes (~1.75 GiB) to reserve plus app sizes.

Backup file IQ3_M (657289344): RAM Files+Assistant = 3126518016 bytes
(~2.91 GiB), headroom ~1.09 GiB. Fits with more margin.

**Pair E (1138135470-byte model: 1135951488 `.pte` + 2183982 tokenizer):**

- Model download: 1138135470 bytes (~1.06 GiB). Under 2 GB. Pass.
- Total download (model + AAR build dependency): 1138135470 + 7584141
  = **1145719611 bytes** (~1.07 GiB). Under the 10 GB cross-worker cap.
- RAM Files+Assistant: 1610612736 + 67108864 + 134217728 + 2276270940
  = **4088210268 bytes** (~3.81 GiB). Headroom to 4 GB: 206757028 bytes
  (~197 MiB). Fits, with much less margin than Pair R.
- RAM Assistant-only: 4021101404 bytes (~3.74 GiB). Headroom ~261 MiB. Fits.
- **6 GB emulator** (6442450944 bytes; the owner's Mac has 16 GB and
  runs one emulator at a time): headroom = 6442450944 - 4088210268
  = **2354240676 bytes** (~2.19 GiB). More than 1 GB of headroom:
  Pair E runs on a 6 GB emulator with margin.
- Storage Files+Assistant: 209715200 + 157286400 + 1138135470
  = 1505137070 bytes (~1.40 GiB). AVD model area per spec 4b (model +
  no extracted copy + 1 GB headroom): 1138135470 + 1073741824
  = 2211877294 bytes (~2.06 GiB) to reserve plus app sizes.

**Pair F (105454432-byte model):**

- Model: 105454432 bytes (~100 MiB). Under cap.
- RAM Files+Assistant: 2022848192 bytes (~1.88 GiB). Headroom ~2.12 GiB.
  Fits easily (reference only; not recommended).

**Upstream Llama safetensors (2471645608):** exceeds 2 GB cap. Excluded.

## 5. Where the files live (spec 4b)

- Route A (image): read-only folder in the system image named at build
  time, e.g. `/product/stargate/model/`. The Assistant app opens the
  `.pte` and `tokenizer.model` **directly from that path** through the
  AAR's Java API (`LlmModule`), with no extracted copy. If
  app-process reads are blocked, that is a recorded Route A Assistant
  failure, not a reason to push after boot.
- Route B (bundle): `/sdcard/Android/data/<package>/files/model/<file>`,
  pushed after boot with recorded `adb push` commands; the app reads
  both files without extra permissions.
- Hash chain (both routes): SHA-256 recorded in build manifest, after Mac
  transfer (`shasum -a 256`), inside the artifact, and by the app at load;
  all four must match. (Pair R paths, if ever used: same layout with the
  single `.gguf` file and `-m <path>`.)

## 6. Recommendation

Two pairs compared on verified downloads (step-0d rule: prefer Pair E
if its minSdk is 34 or lower and the LLM class exists, with a 6 GB
emulator if needed; otherwise Pair R):

- **Pair E (ExecuTorch):** 1.5.1 AAR +
  `executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET`
  at pinned commit `911221e0f49d3354cbd4697a9c09feee25cadec7`,
  called through `LlmModule`. minSdk **23** (emulator is API 34) and
  the LLM class confirmed in the AAR. Model total 1138135470 bytes
  (~1.06 GiB, under cap); fits the 4 GB emulator with ~197 MiB
  headroom and a 6 GB emulator with ~2.19 GiB. Meta's own runtime, a
  real Android library, no child process.
- **Pair R (llama.cpp):** settled build b11721 (18 Oct 2026) +
  Q4_K_M at pinned commit `067b946cf014b7c697f3654f621d577a3e3afd1c`,
  called as a child process. Complete and verified, fits with ~827 MiB
  headroom — but a CLI tarball, not an Android library, and the
  jniLibs child-process route is still to be proven in M4b step 1.

**Recommend Pair E on a 6 GB emulator: ExecuTorch 1.5.1 +
`Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte` + `tokenizer.model`.**

- Meta model (Llama 3.2 1B Instruct SpinQuant INT4, licence llama3.2),
  preferred per spec. Ungated repo; ready-made `.pte`; no conversion.
- All downloads verified against published SHA-256 values.
- Pair R stays the documented alternative. Glimmer is not workable
  (all values unknown; Q15 stays open). SmolLM2 GGUF is a fallback only.

## 7. What the owner must do + INSTALL APPROVAL REQUIRED

Owner actions:

1. ~~Approve the INSTALL list below~~ — done: the owner approved the
   step-0d INSTALL APPROVAL REQUIRED block (both pairs) on 1 Nov 2026.
   Nothing downloaded before approval (spec 4c).
2. No Hugging Face licence acceptance is needed for the recommended files
   (executorch-community repo is ungated; Maven Central is public). The
   Llama 3.2 licence attribution notice ("Llama 3.2 is licensed under
   the Llama 3.2 Community License, Copyright (c) Meta Platforms, Inc.
   All Rights Reserved.") must be kept with distributed copies, as must
   the ExecuTorch BSD copyright notice; M4b will include the notice files.
3. Approve the completed `assistant-manifest.json` below (with verified
   SHA-256 values) before any build uses it (spec 5a).

INSTALL RECORD, Pair E (step 0d; approved, downloaded, and verified):

| # | Item | Source / version | Size | Destination |
|---|---|---|---|---|
| 1 | ExecuTorch Android AAR | https://repo1.maven.org/maven2/org/pytorch/executorch-android/1.5.1/executorch-android-1.5.1.aar ; Maven org.pytorch:executorch-android:1.5.1, BSD | 7584141 bytes; sha256 d67d39eb51e00c996162e1d9898519a622918b20e4e18ea479f5fee4fe920e72 (verified) | /workspaces/m4b-tools/ |
| 2 | Llama 3.2 1B SpinQuant INT4 `.pte` | executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET @ 911221e0f49d3354cbd4697a9c09feee25cadec7, file Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte, licence llama3.2 (Resolve URL: https://huggingface.co/executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET/resolve/911221e0f49d3354cbd4697a9c09feee25cadec7/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte) | 1135951488 bytes; sha256 8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b (verified) | /workspaces/m4b-downloads/ |
| 3 | Llama 3.2 1B tokenizer | same repo @ 911221e0f49d3354cbd4697a9c09feee25cadec7, file tokenizer.model (Resolve URL: https://huggingface.co/executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET/resolve/911221e0f49d3354cbd4697a9c09feee25cadec7/tokenizer.model) | 2183982 bytes; sha256 82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55 (verified) | /workspaces/m4b-downloads/ |

- Conversion tool: none (ready-made file). Pair E downloads total
  1145719611 bytes. (Pair R files were approved and verified under the
  same step-0d approval; recorded in sections 3-4 and the step report.)

## 8. Catalog entry, accepted plan, and manifest

Catalog entry added in step 1A (schema `catalog-v1`, per M3 rules; M3
plan re-run; handoff names this model):

```json
[
  {
    "id": "llama-3.2-1b-instruct-spinquant-int4-eo8-pte",
    "source": "executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET",
    "revision": "911221e0f49d3354cbd4697a9c09feee25cadec7",
    "files": [
      {"name": "Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte", "bytes": 1135951488},
      {"name": "tokenizer.model", "bytes": 2183982}
    ],
    "format": "pte + tokenizer.model; ready-made ExecuTorch SpinQuant INT4 export; loads through LlmModule with file paths",
    "runtime": "executorch 1.5.1 (AAR)",
    "abi": "arm64-v8a",
    "licence": "llama3.2",
    "estimates": ["runtime", "abi"],
    "provenance": {
      "kind": "sourced",
      "url": "https://huggingface.co/executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET",
      "date": "2026-11-01"
    }
  }
]
```

Accepted plan for the build (saved in step 1B; the build runs from
this exact file):

- Path: `demo/plans/flagship-files-assistant.plan.json`
- SHA-256: `1f8f2553fee50c71985d4988e466a943094230cccdf1d64e7f2e584ebb457418`
- Re-run: flagship sim, Files + Assistant, flagship research (offline);
  selection_result fits, build_allowed emulator_only, handoff names
  `llama-3.2-1b-instruct-spinquant-int4-eo8-pte` @ `911221e0…`.

`assistant-manifest.json` (repo root; per spec 5a/11; model ID/revision
exactly as the handoff names them; hashes computed from the verified
downloads in step 1B; APK fields filled in step 1D after the build):

```json
{
  "assistant_apk": {
    "package": "com.stargate.assistant",
    "path": "out/assistant.apk",
    "sha256": "1936549be6da213eb543be672a20fbf5f74c4f2a03de61939d4bbb1a1a95c187",
    "version": "1.0"
  },
  "assistant_cloud_apk": {
    "package": "com.stargate.assistant",
    "path": "out/assistant-cloud.apk",
    "sha256": "fd1812474bed8d6811d39e0bba3c25aeec6dd04a3a013b32a4a9d1c8f48494b6",
    "version": "1.0"
  },
  "dependencies": [
    {
      "bytes": 1718956,
      "name": "kotlin-stdlib",
      "sha256": "8910cc238807d86ef550cb1f0b10dd5ed40b35a4ec1a52525f760aede84ead37",
      "version": "1.9.23"
    },
    {
      "bytes": 4349156,
      "name": "fbjni",
      "sha256": "7e319ae110ac5e5ef18904170aea5c3e753e915d196699d7fd39d36c8e1dfe36",
      "version": "0.7.0"
    },
    {
      "bytes": 3158,
      "name": "nativeloader",
      "sha256": "971ded86009c0ac56fb2275e128f852e7dcbc41a40cd799688f8a1aebde5195d",
      "version": "0.10.5"
    }
  ],
  "emulator_ram_bytes": 6442450944,
  "files": [
    {
      "bytes": 1135951488,
      "name": "Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte",
      "sha256": "8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b"
    },
    {
      "bytes": 2183982,
      "name": "tokenizer.model",
      "sha256": "82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55"
    }
  ],
  "licence_note": "Llama 3.2 Community License + ExecuTorch BSD; keep attribution notices with copies",
  "min_android": "android-23",
  "model_id": "llama-3.2-1b-instruct-spinquant-int4-eo8-pte",
  "plan_path": "demo/plans/flagship-files-assistant.plan.json",
  "plan_sha256": "1f8f2553fee50c71985d4988e466a943094230cccdf1d64e7f2e584ebb457418",
  "ram_bytes_files_assistant": 4088210268,
  "runtime_aar": {
    "bytes": 7584141,
    "maven": "org.pytorch:executorch-android:1.5.1",
    "name": "executorch-android-1.5.1.aar",
    "sha256": "d67d39eb51e00c996162e1d9898519a622918b20e4e18ea479f5fee4fe920e72"
  },
  "runtime_version": "executorch 1.5.1",
  "source_revision": "911221e0f49d3354cbd4697a9c09feee25cadec7",
  "target_path_route_a": "/product/stargate/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte",
  "target_path_route_b": "/sdcard/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte",
  "tokenizer_path_route_a": "/product/stargate/model/tokenizer.model",
  "tokenizer_path_route_b": "/sdcard/Android/data/com.stargate.assistant/files/model/tokenizer.model",
  "tools_dir": "../m4b-tools"
}
```

## 9. Part D build record (1 Nov 2026)

- Command: `flock /workspaces/.android-build.lock
  /usr/local/bin/python3 assistant-app/build.py` (shared lock, single
  process; `ps` showed no other Android/Gradle build first). No Gradle:
  the AARs are used directly (classes.jar compiled + dexed,
  `jni/<abi>/*.so` into `lib/`).
- Toolchain (read-only `/workspaces/m4-tools`): JDK 17 (`javac
  --release 8`), build-tools 34.0.0 (`d8 --min-api 23`, `aapt2`,
  `zipalign`, `apksigner`), android-34 `android.jar`.
- App: `com.stargate.assistant` 1.0 (code 1), minSdk 23, targetSdk 34,
  **no permissions** (badging shows none). Loads the `.pte` +
  `tokenizer.model` by file path through `LlmModule` (Route B folder
  first, then Route A); creates the Route B folder when missing
  (Route A is only opened in place, never created or copied);
  reports per-path check failures (folder missing, file missing, not
  readable, wrong size) with the expected names and sizes;
  SHA-256-checks both files against manifest-baked values before
  loading and shows "not checked" until it has. Sends the question in
  the Llama 3.2 Instruct chat format (section 11), stops generation
  at the end-of-turn and end-of-text tokens, filters control-token
  text from the answer, caps answers at 256 new tokens, and shows one
  stats line ("Offline · N tokens · S s") with the raw stats behind
  a Details button. Every question runs through AskFlow: device-fact
  questions are answered by the app without calling the model
  (stats line "Answered by the app"), and every other question
  resets the runner first so each starts from an empty context.
  Visible title and label are "Assistant" (the package name is
  unchanged).
- APK: `/workspaces/m4b-tools/out/assistant.apk`, 24765321 bytes,
  SHA-256
  `f98a59a5629e30cf29222804900ac12176621a318f53c34297baa031ad520439`
  (rebuilt in fixup 4; out of git, hash-pinned by the manifest).
  Native libs for arm64-v8a + x86_64 (`libexecutorch.so`,
  `libfbjni.so`, `libc++_shared.so`, stored + aligned). No model
  file; only asset is `assets/licenses.txt` (=
  `third_party/NOTICES.md`, shown from the app's Licenses button).
  Signed with the step-1D debug key
  (`/workspaces/m4b-tools/assistant-debug.keystore`, not in the repo).
- Tests: full offline suite 359/359 OK, including 58
  `tests/test_assistant.py` tests (the 43 from before: no permission
  in built manifest, no model in APK with licenses allowed +
  synthetic allow/refuse cases, licenses asset matches NOTICES.md,
  both ABI lib sets, hash check refuses same-name/same-size
  different-content via the real Java class on a JVM, manifest binds
  to handoff + 5 tamper cases, APK fields match the built file,
  dependencies match downloads; chat prompt golden string, token cap
  value + single-setting wiring, no product branding, 4
  generation-stop and 4 display-filter cases with a fake chunk feed,
  Route B folder creation + per-check failures including unreadable
  cases, 3 stats-line cases, and 8 device-guard cases against a fake
  adb; plus 15 new: reset-before-every-generate with a fake runner
  recording call order and start positions, device-fact question
  never touching the runner, 3 catch + 2 ignore + substring +
  phrase cases for the device-fact check, app answer and stats-line
  text, and AskFlow/MainActivity wiring).

## 10. Part E Mac proof steps (owner-run; written, not run)

Quick pre-integration proof of the Assistant APK + model on a fresh
emulator. Route B (push after boot). All work under `~/stargate-m4/`.
These steps run AFTER the spike's Mac test and reuse the SDK,
emulator and system image it installed; they add NO new installs.
If the image below is missing, that is "needs an approved install",
not a command here. Every block pastes cleanly into zsh (one command
per line; no angle brackets anywhere). No real phones, USB, or `fastboot`.

Device guard: after `SERIAL` is set, EVERY adb command below is
prefixed `guard &&`, except the read-only `adb devices` inventory
listings (the discovery listing at the top of each block and the
final listing after `emu kill` need no guard: they change nothing,
and the final one runs when no device should remain). Paste the
guard once per shell; it is tested in `tests/test_assistant.py`
against a fake adb:

```sh
# check-device guard: one inventory, strict single-device match
guard() {
  if [ -z "$SERIAL" ]; then
    echo "guard: SERIAL is not set" >&2
    return 1
  fi
  _inv=$(adb devices | tr -d '\r')
  _rows=$(printf '%s\n' "$_inv" | tail -n +2 | grep -c . || true)
  if [ "$_rows" != "1" ]; then
    echo "guard: expected exactly one device row, saw $_rows" >&2
    return 1
  fi
  _row=$(printf '%s\n' "$_inv" | tail -n +2 | grep .)
  _want=$(printf '%s\tdevice' "$SERIAL")
  if [ "$_row" != "$_want" ]; then
    echo "guard: device row is not [$SERIAL device]" >&2
    return 1
  fi
  _avd=$(adb -s "$SERIAL" emu avd name | tr -d '\r')
  if [ "$_avd" != "stargate-m4b-assistant" ]; then
    echo "guard: AVD is not stargate-m4b-assistant" >&2
    return 1
  fi
}
```

The guard reads ONE `adb devices` inventory, counts EVERY row
whatever its state, and passes only when there is exactly one row
equal to `$SERIAL`, a tab, then `device`, and `adb -s "$SERIAL" emu
avd name` is `stargate-m4b-assistant`. If it fails, stop and fix the
device list before running anything else. It runs before every
command, so it also re-checks after each adbd reconnect (root/unroot
restart adbd; if the guard denies while the emulator is still
re-attaching, wait a moment and re-paste the line).

0. **Cleanup list first.** Before creating anything, add a row to
   `~/stargate-cleanup-log.md`: AVD `stargate-m4b-assistant`, undo
   `avdmanager delete avd -n stargate-m4b-assistant` and delete
   `~/.android/avd/stargate-m4b-assistant.avd/`; files under
   `~/stargate-m4/`, undo `rm`.
1. **AVD.** Stock `system-images;android-34;default;arm64-v8a`
   (already installed by the spike), same device as the spike
   (`pixel_7`; if rejected, `avdmanager list device` shows the
   available names — pick the closest Pixel):

   ```sh
   echo no | avdmanager create avd -n stargate-m4b-assistant -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
   ```

2. **AVD sizing.** Model (1138135470 bytes) + 1 GB = 2211877294
   bytes minimum; set 6 GB RAM and a 4 GB data partition in the
   AVD's `config.ini` (exact lines `hw.ramSize=6144` and
   `disk.dataPartition.size=4G`):

   ```sh
   CFG=~/.android/avd/stargate-m4b-assistant.avd/config.ini
   test -f "$CFG"
   sed -i '' '/^hw.ramSize=/d; /^disk.dataPartition.size=/d' "$CFG"
   printf '%s\n' 'hw.ramSize=6144' 'disk.dataPartition.size=4G' | tee -a "$CFG"
   grep -E '^(hw.ramSize|disk.dataPartition.size)=' "$CFG"
   ```

3. **Launch** (record the exact command), then wait for boot:

   ```sh
   emulator -avd stargate-m4b-assistant -no-snapshot -wipe-data
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" wait-for-device
   guard && until [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed)" = 1 ]; do sleep 5; done
   ```

4. **Transfer + check.** Download these four files from the codespace
   to `~/stargate-m4/` through the browser (APK from
   `/workspaces/m4b-tools/out/assistant.apk`, notices from
   `third_party/NOTICES.md`), then check each:

   ```sh
   shasum -a 256 ~/stargate-m4/assistant.apk
   shasum -a 256 ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte
   shasum -a 256 ~/stargate-m4/tokenizer.model
   shasum -a 256 ~/stargate-m4/NOTICES.md
   ```

   Expected (a mismatch means stop): `assistant.apk`
   `f98a59a5629e30cf29222804900ac12176621a318f53c34297baa031ad520439`
   (from `assistant-manifest.json`), `.pte`
   `8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b`,
   `tokenizer.model`
   `82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55`,
   `NOTICES.md`
   `7e95dcb163ee469d2ad839de3336ea736ea0b45dc0e09daebdb94b60d21f0aa1`.
5. **Install**, then re-wait for boot. The stock image can restart
   once on first boot (the Mac proof saw system_server crash with a
   gatekeeper NullPointerException right after `-wipe-data`), so
   boot is confirmed again here:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" install ~/stargate-m4/assistant.apk
   guard && adb -s "$SERIAL" wait-for-device
   guard && until [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed)" = 1 ]; do sleep 5; done
   ```

6. **First launch, then push the model files.** The app creates its
   own Route B folder on first launch; never `mkdir` it from the
   shell, and never `rm` the app's folder while the app is running:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell am start -W -n com.stargate.assistant/.MainActivity
   guard && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
   guard && adb -s "$SERIAL" shell ls -ld /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" push ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" push ~/stargate-m4/tokenizer.model /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" shell sha256sum /sdcard/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/tokenizer.model
   ```

   The two on-device hashes must match step 4. If the `ls` line shows
   the folder is missing, stop: the app did not create it (its error
   screen says which check failed).
7. **Networking off**, then confirm the status bar shows no
   connectivity:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell svc wifi disable
   guard && adb -s "$SERIAL" shell svc data disable
   ```

8. **Ask.** Launch with `am start` (not monkey); read the two
   on-screen hashes (must match step 4) with the model ID and the
   Offline line; ask one question; note the answer and its time:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
   ```

9. **If the app still says not found (EMULATOR ONLY, never a real
   phone).** The app's error screen says which check failed. When the
   folder exists but the app cannot read it (foreign ownership left
   by an earlier shell-made folder), re-push as root, hand the files
   to the uid and gid of the app's `files` folder, fix the SELinux
   labels, then drop root and relaunch:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
   guard && adb -s "$SERIAL" root
   guard && adb -s "$SERIAL" wait-for-device
   guard && adb -s "$SERIAL" push ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /data/media/0/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" push ~/stargate-m4/tokenizer.model /data/media/0/Android/data/com.stargate.assistant/files/model/
   guard && FUID=$(adb -s "$SERIAL" shell stat -c %u /data/media/0/Android/data/com.stargate.assistant/files | tr -d '\r')
   guard && FGID=$(adb -s "$SERIAL" shell stat -c %g /data/media/0/Android/data/com.stargate.assistant/files | tr -d '\r')
   guard && adb -s "$SERIAL" shell chown "$FUID:$FGID" /data/media/0/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /data/media/0/Android/data/com.stargate.assistant/files/model/tokenizer.model
   guard && adb -s "$SERIAL" shell restorecon -R /data/media/0/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" unroot
   guard && adb -s "$SERIAL" wait-for-device
   guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
   ```

10. **Proof.** `wifi_on` must print `0`; the screenshot keeps the
    answer with the hashes on screen; then the emulator is killed:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell settings get global wifi_on
   guard && adb -s "$SERIAL" shell screencap -p /sdcard/m4b-proof.png
   guard && adb -s "$SERIAL" pull /sdcard/m4b-proof.png ~/stargate-m4/m4b-proof.png
   guard && adb -s "$SERIAL" emu kill
   adb devices
   ```

   Record every command run and its result. This AVD is proof-only
   and stays out of the M4 demo builds.

## 11. Fixup 3 notes: prompt format, stop tokens, end-of-sequence

- Chat format source (on disk, quoted, not guessed):
  `/workspaces/m4b-downloads/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
  (same Llama 3.2 1B Instruct model family, licence llama3.2),
  metadata keys `tokenizer.chat_template` (Meta's Llama 3.2 jinja
  template), `tokenizer.ggml.tokens`, `tokenizer.ggml.bos_token_id`
  (128000) and `tokenizer.ggml.eos_token_id` (128009). Token IDs:
  128000 `<|begin_of_text|>`, 128001 `<|end_of_text|>`, 128006
  `<|start_header_id|>`, 128007 `<|end_header_id|>`, 128009
  `<|eot_id|>`. Rendered prompt (system + one user turn + generation
  prompt): `<|begin_of_text|>` + `<|start_header_id|>system` +
  `<|end_header_id|>` + blank line + system message + `<|eot_id|>` +
  `<|start_header_id|>user` + `<|end_header_id|>` + blank line +
  question + `<|eot_id|>` + `<|start_header_id|>assistant` +
  `<|end_header_id|>` + blank line. The template's "Cutting Knowledge
  Date / Today Date" preamble is omitted on purpose: the assistant
  must not claim time knowledge, and the template's fallback date
  would be stale. Caveat: the GGUF is the Pair R quant of the same
  upstream instruct model, not the Pair E SpinQuant export itself;
  the executorch-community README (never downloaded, not device
  payload) would be authoritative for the `.pte`.
- End-of-sequence inspection. Runner: `LlmModule` and
  `LlmGenerationConfig` in `executorch-android-1.5.1.aar` offer
  echo/maxNewTokens/warming/seqLen/temperature/numBos/numEos but no
  stop-token-ID list (javap of the AAR's `classes.jar`); the native
  library carries `get_eos_ids`, an `eos_id` log line, and the
  `<|begin_of_text|>`, `<|end_of_text|>`, `<|start_header_id|>`,
  `<|eot_id|>` literals, so EOS handling exists natively but its
  exact token set is not documented on disk. The Mac proof showed
  the runner delivers the terminating token's text to the callback
  before its end-of-sequence check. Model: the Pair E
  `tokenizer.model` on disk holds only IDs 0-127999 (no special
  tokens); the `.pte` embeds no template strings. App guarantee,
  two separate fixes: (a) when a stop marker (end-of-turn or
  end-of-text) arrives in the callback stream the app stops the
  runner, so generation ends at either token; (b) the displayed
  answer is cut at the first stop marker and the other known
  control tokens are removed, so control-token text never shows,
  even when the callback delivers the terminating token itself.
- Sampling: no temperature/top-p defaults are on disk (the GGUF
  metadata carries none; the model card was never fetched), so
  temperature keeps 0.8 (also the config builder's default). New
  tokens are capped at 256 in one setting
  (`ChatFormat.MAX_NEW_TOKENS`).
- Route B only: the app creates the Route B folder when missing and
  reports creation/readability failures per path; Route A's
  read-only path is only opened in place. mkdirs cannot repair a
  folder owned by someone else, so nothing claims the folder is
  app-owned.
- Stats line keys (`"prompt_tokens"`, `"generated_tokens"`) are the
  native library's stats JSON keys (same `.so`); the app-measured
  time is shown without a count when the count is absent instead of
  inventing one.
- Fixup 4, fresh context per question. The Mac log showed positions
  climbing (60, 134, 236 ... 928) because nothing reset the runner
  between questions. `LlmModule` in the 1.5.1 AAR exposes
  `public final void resetContext();` (javap of the AAR's
  `classes.jar`, class
  `org.pytorch.executorch.extension.llm.LlmModule`); every question
  now runs through `AskFlow.ask`, which calls the runner's reset
  before its generate, so each question starts from an empty
  context. Ordering is tested with a fake runner recording the
  call order and simulated start positions.
- Fixup 4, app-side device-fact answers. The 1B model invented "73%"
  for a battery question despite the system message, so questions
  about this phone's battery, charge, time or date, location,
  signal or network, storage, or sensors never reach the model:
  `DeviceFacts` matches one whole-word case-insensitive trigger
  list and the app shows "I'm an offline assistant and can't see
  the phone's battery, sensors, files, time or network." with the
  stats line "Answered by the app". Bare "battery" is deliberately
  not a trigger (general-knowledge questions like "Explain what a
  battery does" still reach the model); only battery
  state-of-charge phrases match.

## 12. M4c part 1: cloud variant, offline (1 Nov 2026)

Spec: `plan/milestones/M4c-assistant-cloud-mode.md` v2.4. No
live calls, no keys read or written, no downloads or installs.
Branch `m4c-cloud`, two commits (1A: cloud variant, providers
and keys; 1B: ledger, gate and docs).

- Two variants of `com.stargate.assistant` 1.0 (code 1),
  one per build, from `assistant-app/build.py --variant`
  (refuses any other permission set):
  - local `/workspaces/m4b-tools/out/assistant.apk`,
    24777688 bytes, SHA-256
    `1936549be6da213eb543be672a20fbf5f74c4f2a03de61939d4bbb1a1a95c187`,
    no permissions, fixup-4 behaviour (no Model drop-down);
  - cloud
    `/workspaces/m4b-tools/out/assistant-cloud.apk`,
    24777688 bytes, SHA-256
    `fd1812474bed8d6811d39e0bba3c25aeec6dd04a3a013b32a4a9d1c8f48494b6`,
    exactly `android.permission.INTERNET`.
  Both pinned in `assistant-manifest.json`
  (`assistant_apk`, `assistant_cloud_apk`). Both set
  `allowBackup="false"` with a data-extraction rule excluding
  every domain from cloud backup and device transfer.
- Three modes in one Model drop-down (cloud variant; Local
  is the default): Local (the handoff model, on the phone),
  [Historical (v2.4, replaced by own-key v2.5): the "priced
  ID" below is stale; current rules:
  docs/m4c-integration.md.]
  Meta (`muse-spark-1.3` default, user may type another
  priced ID), Meta open-weight (exactly
  `meta-models/muse-spark-open-1026`, fixed, via Baseten).
  The screen always shows which mode and model answered.
  Device-fact questions are answered by the app in every
  mode; local mode resets context per question and makes
  zero network requests (fake-layer test); cloud modes send
  only the current question with the same short system
  message, never history.
- Providers pinned per spec section 3: Meta
  `POST https://api.meta.ai/v1/chat/completions` with
  `max_completion_tokens`, Baseten
  `POST https://inference.baseten.co/v1/chat/completions`
  with `max_tokens`, each key only to its own host,
  redirects refused, TLS defaults on, the error table with
  its retry rules (429 once after the asked wait capped at
  10 s, 5xx/timeout once, nothing else retried, no model or
  provider ever substituted). Caps: 2,000 chars and 8,000
  bytes per question, 16 KB body, 4,096 output tokens,
  256 KB reply cut-off with label, 60 s per request
  including the retry.
- Keys: pasted at runtime, encrypted with a Keystore
  AES-GCM key per provider, no plain-text fallback (a
  Keystore failure saves nothing and Ask sends nothing);
  settings show the last four characters plus Delete; logs
  and the Details line hold metadata only.
[Historical (v2.4, replaced by own-key v2.5): the ledger,
priced share and filled-in prices below are stale; current
rules: docs/m4c-integration.md and docs/m4c-calls.md.]

- Ledger and gate (section 6): `docs/m4c-ledger.json` is
  the only source of truth (5 attempts per provider,
  $5 Baseten / $2 Meta ledger limits, owner's recorded
  credit Meta $145 / Baseten $100 from 1 Nov 19:54, 32,768
  input-token ceiling, reserve-before-send, retries as new
  attempts, usage-above-reservation halts the provider);
  `docs/m4c-calls.md` is generated from it. Prices are null
  with source "unknown, owner fills in before the first
  live call", so the gate refuses every live call; each
  cloud build reserves 2 attempts per provider under its
  ID, bakes the priced-model list into the APK, and the app
  refuses a third call on its share. Codespace library:
  `stargate/m4c.py`.
- Tests: full offline suite green (all 359 pre-existing
  plus the new `tests/test_m4c.py`: provider pins, error
  table, credential routing, redirects, caps, share,
  Keystore failure, privacy, logs, ledger and gate rows
  refused before a fake network layer, both APKs'
  permissions read back, backup rules from the built APKs,
  proof-doc guard lines). The two key scans (AAR class refs,
  native NEEDED) plus a key-shaped-string scan over both
  APKs and all docs are clean.
- Builder contract (`stargate build --assistant-cloud`,
  build-v1 fields, share reservation, contents check of real
  permissions with exit 8, key scan over APKs): exact steps
  in `docs/m4c-integration.md`. Owner proof steps (fresh
  6 GB AVD `stargate-m4c-cloud`, dummy keys, both backup
  checks, one local + two live answers): written, not run,
  in `docs/m4c-mac-proof.md`; live steps need the owner's OK
  and filled-in prices.
